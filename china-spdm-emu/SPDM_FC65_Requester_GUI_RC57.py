#!/usr/bin/env python3
"""SPDM FC65 Requester GUI RC57

A diagnostic SPDM requester for a custom Modbus RTU function 0x65 transport.
The FC65 wrapper is configurable because function 0x65 is vendor-defined.

Default TX frame:
    slave(1) | 0x65 | byte_count(1) | SPDM | CRC16(Modbus, little-endian)
Default RX frame is assumed to use the same layout.
"""
from __future__ import annotations

import binascii
import hashlib
import os
from pathlib import Path
import queue
import struct
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None
try:
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils
except ImportError:
    x509 = None
    InvalidSignature = Exception
    hashes = None
    serialization = None
    ec = None
    utils = None

# Requester capabilities used by the authentication/measurement flow.
# CERT_CAP | CHAL_CAP | MEAS_CAP_SIG = 0x00000016.
SPDM_CERT_CAP = 0x00000002
SPDM_CHAL_CAP = 0x00000004
SPDM_MEAS_CAP_SIG = 0x00000010
SPDM_BASE_REQUESTER_CAPS = SPDM_CERT_CAP | SPDM_CHAL_CAP | SPDM_MEAS_CAP_SIG
SPDM_CHUNK_CAP = 0x00020000
# Advertise the maximum uint32 logical-message capacity during GET_CAPABILITIES.
# After CAPABILITIES is received, all subsequent decisions use the responder's
# advertised limits, which become the effective limits because this requester
# advertises the maximum possible MaxSPDMmsgSize.
SPDM_REQUESTER_MAX_SPDM_MSG_SIZE = 0xFFFFFFFF
SPDM_FC65_MAX_SPDM_PAYLOAD = 0xFF
SPDM_CERTIFICATE_RESPONSE_HEADER_SIZE = 8
SPDM_CODES = {
    0x84: "GET_VERSION", 0x04: "VERSION",
    0xE1: "GET_CAPABILITIES", 0x61: "CAPABILITIES",
    0xE3: "NEGOTIATE_ALGORITHMS", 0x63: "ALGORITHMS",
    0x81: "GET_DIGESTS", 0x01: "DIGESTS",
    0x82: "GET_CERTIFICATE", 0x02: "CERTIFICATE",
    0x83: "CHALLENGE", 0x03: "CHALLENGE_AUTH",
    0xE0: "GET_MEASUREMENTS", 0x60: "MEASUREMENTS",
    0xFF: "RESPOND_IF_READY", 0x7F: "ERROR",
    0x85: "CHUNK_SEND", 0x05: "CHUNK_SEND_ACK",
    0x86: "CHUNK_GET", 0x06: "CHUNK_RESPONSE",
}
ERROR_CODES = {
    # DSP0274 wire values. RC11 had several values shifted by one.
    0x01: "INVALID_REQUEST", 0x03: "BUSY", 0x04: "UNEXPECTED_REQUEST",
    0x05: "UNSPECIFIED", 0x06: "DECRYPT_ERROR", 0x08: "REQUEST_IN_FLIGHT",
    0x09: "INVALID_RESPONSE_CODE", 0x0A: "SESSION_LIMIT_EXCEEDED",
    0x0B: "SESSION_REQUIRED", 0x0C: "RESET_REQUIRED",
    0x0D: "RESPONSE_TOO_LARGE", 0x0E: "REQUEST_TOO_LARGE",
    0x0F: "LARGE_RESPONSE", 0x10: "MESSAGE_LOST",
    0x11: "INVALID_POLICY", 0x41: "VERSION_MISMATCH",
    0x42: "RESPONSE_NOT_READY", 0x43: "REQUEST_RESYNCH",
    0x44: "OPERATION_FAILED", 0x45: "NO_PENDING_REQUESTS",
}


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def hx(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_hex(text: str) -> bytes:
    clean = text.replace("0x", "").replace(",", " ").replace("_", " ")
    return bytes.fromhex(clean)


@dataclass
class PendingRequest:
    request_code: int = 0
    token: int = 0
    rd_exponent: int = 0
    rdtm: int = 0
    ready_at: float = 0.0
    generation: int = 0
    cycle: int = 0


class FC65Codec:
    def __init__(self, slave: int, endian: str, length_bytes: int, crc: bool):
        self.slave = slave
        self.endian = endian
        self.length_bytes = length_bytes
        self.crc = crc

    def wrap(self, payload: bytes) -> bytes:
        if len(payload) > 0xFF:
            raise ValueError(f"SPDM payload too large for one-byte FC65 ByteCount: {len(payload)}")
        body = bytes((self.slave, 0x65, len(payload))) + payload
        if self.crc:
            body += struct.pack("<H", crc16_modbus(body))
        return body

    def unwrap(self, frame: bytes) -> tuple[bytes, list[str]]:
        notes = []
        minimum = 3 + (2 if self.crc else 0)
        if len(frame) < minimum:
            raise ValueError(f"FC65 frame too short: {len(frame)}")
        if frame[1] != 0x65:
            raise ValueError(f"Unexpected function 0x{frame[1]:02X}")
        if self.crc:
            got = int.from_bytes(frame[-2:], "little")
            expected = crc16_modbus(frame[:-2])
            notes.append(f"CRC received=0x{got:04X}, calculated=0x{expected:04X}")
            if got != expected:
                raise ValueError(notes[-1] + " (mismatch)")
        end = len(frame) - 2 if self.crc else len(frame)
        declared = frame[2]
        payload = frame[3:end]
        notes.append(f"FC65 ByteCount={declared}, SPDM payload bytes={len(payload)}")
        if declared != len(payload):
            raise ValueError(f"FC65 ByteCount mismatch: declared={declared}, received={len(payload)}")
        return payload, notes


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SPDM Requester over COM / FC65 RC57 - FULL AUTO FLOW")
        self.geometry("1320x860")
        self.minsize(1120, 720)
        self.ser = None
        self.q = queue.Queue()
        self.pending = PendingRequest()
        self.pending_generation = 0
        self.pending_cycle = 0
        self.respond_ready_after_id = None
        self.respond_ready_in_flight = False
        self.io_lock = threading.Lock()
        self.request_done = threading.Event()
        self.logical_done = threading.Event()
        self.sequence_running = False
        self.measurement_block_count = None
        self.auto_respond_ready = tk.BooleanVar(value=True)
        self.cert = bytearray()
        self.cert_offset = 0
        self.cert_chunk_test = False
        self.chunk_handle = 0
        self.chunk_seq = 0
        self.chunk_message = bytearray()
        self.chunk_expected_size = None
        self.chunk_transfer_active = False
        self.chunk_original_request_code = 0
        # RC25: immutable parent request for a LargeResponse/CHUNK transaction.
        # This is captured when the logical request is transmitted and is never
        # overwritten by CHUNK_GET, a stale RNR record, or a later GUI stage.
        self.logical_request_in_flight = 0
        self.requester_flags = 0
        self.responder_flags = 0
        self.requester_data_transfer_size = SPDM_FC65_MAX_SPDM_PAYLOAD
        self.requester_max_spdm_msg_size = SPDM_REQUESTER_MAX_SPDM_MSG_SIZE
        self.responder_data_transfer_size = None
        self.responder_max_spdm_msg_size = None
        self.effective_data_transfer_size = None
        self.effective_max_spdm_msg_size = None
        self.last_tx_spdm = b""
        self.last_rx_spdm = b""
        # Exact VCA wire transcript for SPDM 1.2+ measurement verification.
        self.vca_transcript = bytearray()
        self.auth_transcript = bytearray()
        self.cert_transcript_exchanges = []
        self.last_challenge_request = b""
        # Connection-level authentication state used to mirror the SPDM
        # Message B/C reset rule for pre-authentication GET_MEASUREMENTS.
        self.connection_authenticated = False
        self.last_measurements_request = b""
        self.last_measurement_context = b""
        self.staged_get_digests_request = b""
        # Cumulative L1/L2 bytes for a multiple-request measurement transcript.
        # VCA is kept separately and prepended only when verifying a signature.
        self.measurement_transcript = bytearray()
        self.pending_large_request = b""
        self.chunk_parent_request = b""
        # RC56 keeps immutable snapshots for binary export and byte comparison.
        self.export_message_b = b""
        self.export_logical_certificate = b""
        self.logical_request_in_flight = 0
        self.chunk_transfer_active = False
        self.chunk_original_request_code = 0
        self.chunk_message.clear()
        self.chunk_expected_size = None
        self.device_certificate = None
        self.certificate_chain_verified = False
        # RC44 compatibility mode follows permissive requesters: malformed X.509
        # extensions are reported, but DER framing, RootHash, chain signatures,
        # and the leaf P-384 public key are validated independently.
        self.ignore_certificate_format_errors = tk.BooleanVar(value=True)
        self.certificate_format_warning = False
        self.certificate_leaf_link_warning = False
        self.device_public_key = None
        # RC36 trusted Root CA file and independent comparison result.
        self.trusted_root_ca_der = None
        self.trusted_root_ca_path = ""
        self.root_ca_match = None
        self.full_flow_failures = []
        self._build()
        self.refresh_ports()
        self.after(50, self._pump)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build(self):
        top = ttk.LabelFrame(self, text="COM Port")
        top.pack(fill="x", padx=8, pady=6)
        self.port = tk.StringVar(); self.baud = tk.StringVar(value="115200")
        self.parity = tk.StringVar(value="N"); self.timeout = tk.StringVar(value="1.0")
        for label, var, width in [("Port", self.port, 14), ("Baud", self.baud, 10), ("Parity", self.parity, 5), ("Timeout(s)", self.timeout, 7)]:
            ttk.Label(top, text=label).pack(side="left", padx=(8,2))
            if label == "Port": self.port_combo = ttk.Combobox(top, textvariable=var, width=width)
            elif label == "Parity": self.port_combo2 = ttk.Combobox(top, textvariable=var, values=["N","E","O"], width=width, state="readonly")
            else: self.port_combo2 = ttk.Entry(top, textvariable=var, width=width)
            (self.port_combo if label == "Port" else self.port_combo2).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_ports).pack(side="left", padx=5)
        self.connect_btn = ttk.Button(top, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side="left", padx=5)
        self.status = ttk.Label(top, text="Disconnected", foreground="#a00000")
        self.status.pack(side="left", padx=8)

        cfg = ttk.LabelFrame(self, text="FC65 Wrapper (adjust to match firmware)")
        cfg.pack(fill="x", padx=8, pady=3)
        self.slave = tk.StringVar(value="80"); self.lenbytes = tk.IntVar(value=1)
        self.endian = tk.StringVar(value="little"); self.use_crc = tk.BooleanVar(value=True)
        ttk.Label(cfg, text="Slave hex").pack(side="left", padx=(8,2)); ttk.Entry(cfg,textvariable=self.slave,width=5).pack(side="left")
        ttk.Label(cfg, text="ByteCount bytes").pack(side="left", padx=(10,2)); ttk.Combobox(cfg,textvariable=self.lenbytes,values=[1],width=4,state="readonly").pack(side="left")
        ttk.Label(cfg, text="Length endian").pack(side="left", padx=(10,2)); ttk.Combobox(cfg,textvariable=self.endian,values=["little","big"],width=7,state="readonly").pack(side="left")
        ttk.Checkbutton(cfg,text="Modbus CRC16",variable=self.use_crc).pack(side="left",padx=10)
        ttk.Checkbutton(cfg,text="Auto RESPOND_IF_READY",variable=self.auto_respond_ready).pack(side="left",padx=10)
        self.advertise_chunk = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg,text="Requester CHUNK_CAP",variable=self.advertise_chunk).pack(side="left",padx=10)
        ttk.Checkbutton(
            cfg, text="Ignore malformed certificate extensions (compatibility)",
            variable=self.ignore_certificate_format_errors).pack(side="left", padx=10)
        ttk.Label(cfg,text="Format: 80 65 ByteCount SPDM CRC16; RX validates ByteCount").pack(side="left",padx=12)

        main = ttk.Panedwindow(self, orient="horizontal"); main.pack(fill="both", expand=True, padx=8, pady=5)
        left = ttk.Frame(main); right = ttk.Frame(main); main.add(left, weight=2); main.add(right, weight=3)

        req = ttk.LabelFrame(left, text="SPDM steps (SPDM 1.0 / 1.1 / 1.2 / 1.3)"); req.pack(fill="x", pady=2)
        self.version = tk.StringVar(value="12")
        row = ttk.Frame(req); row.pack(fill="x", padx=5, pady=4)
        ttk.Label(row,text="Version byte").pack(side="left"); ttk.Entry(row,textvariable=self.version,width=5).pack(side="left",padx=4)
        buttons = [
            ("1. GET_VERSION", self.get_version), ("2. GET_CAPABILITIES", self.get_caps),
            ("3. NEGOTIATE_ALGORITHMS", self.negotiate), ("4. GET_DIGESTS", self.get_digests),
            ("5. GET_CERTIFICATE auto (Requester CHUNK_CAP decides)", self.get_cert_auto),
            ("6. CHALLENGE", self.challenge),
            ("7. GET_MEASUREMENTS", self.measurements), ("8. RESPOND_IF_READY", self.respond_ready),
        ]
        for text, cmd in buttons:
            ttk.Button(req,text=text,command=cmd).pack(fill="x",padx=7,pady=2)
        ttk.Separator(req).pack(fill="x",pady=5)
        ttk.Button(req,text="Run discovery sequence 1-4",command=self.run_sequence).pack(fill="x",padx=7,pady=2)
        self.full_sequence_btn = ttk.Button(
            req,
            text="RC56 FULL AUTO: VERSION -> CERT -> CHALLENGE -> MEASUREMENTS",
            command=self.run_full_sequence)
        self.full_sequence_btn.pack(fill="x", padx=7, pady=(10, 4), ipady=5)

        opt = ttk.LabelFrame(left,text="Request parameters"); opt.pack(fill="x",pady=6)
        self.slot=tk.IntVar(value=0); self.cert_len=tk.IntVar(value=240); self.meas_index=tk.IntVar(value=255)
        self.meas_attr=tk.IntVar(value=1); self.nonce=tk.StringVar(value=os.urandom(32).hex().upper())
        # SPDM 1.3 CHALLENGE carries the mandatory 8-byte RequesterContext.
        # Keep one context value for both the original request and any RIR replay.
        self.requester_context = os.urandom(8)
        for label,var in [("Slot",self.slot),("Certificate chunk",self.cert_len),("Measurement index",self.meas_index),("Measurement attributes",self.meas_attr)]:
            r=ttk.Frame(opt);r.pack(fill="x",padx=5,pady=2);ttk.Label(r,text=label,width=22).pack(side="left");ttk.Entry(r,textvariable=var,width=12).pack(side="left")
        r=ttk.Frame(opt);r.pack(fill="x",padx=5,pady=2);ttk.Label(r,text="Nonce (32 bytes hex)",width=22).pack(side="left");ttk.Entry(r,textvariable=self.nonce).pack(side="left",fill="x",expand=True)
        ttk.Button(opt,text="New random nonce",command=lambda:self.nonce.set(os.urandom(32).hex().upper())).pack(padx=5,pady=3,anchor="e")

        raw = ttk.LabelFrame(left,text="Raw SPDM request"); raw.pack(fill="both",expand=True,pady=2)
        self.raw=tk.Text(raw,height=5,wrap="word");self.raw.pack(fill="both",expand=True,padx=5,pady=4)
        ttk.Button(raw,text="Send raw SPDM",command=self.send_raw).pack(pady=3)

        logbox=ttk.LabelFrame(right,text="TX / RX decode log");logbox.pack(fill="both",expand=True)
        # RC36: wrap long decode-log lines at word boundaries so FC65/SPDM
        # hex dumps and long status messages remain visible without horizontal
        # scrolling. Existing explicit newlines and vertical scrolling remain.
        self.log=tk.Text(logbox,wrap="word",font=("Consolas",10))
        self.log.pack(fill="both",expand=True,side="left")
        sy=ttk.Scrollbar(logbox,orient="vertical",command=self.log.yview)
        sy.pack(side="right",fill="y")
        self.log.configure(yscrollcommand=sy.set)
        trust=ttk.LabelFrame(right,text="Trusted Root CA (.der)");trust.pack(fill="x",pady=4)
        self.root_ca_path_var=tk.StringVar(value="No Root CA selected")
        ttk.Button(trust,text="Open Root CA DER",command=self.open_root_ca_der).pack(side="left",padx=5,pady=4)
        ttk.Label(trust,textvariable=self.root_ca_path_var).pack(side="left",fill="x",expand=True,padx=5)
        actions=ttk.Frame(right);actions.pack(fill="x",pady=4)
        ttk.Button(actions,text="Clear log",command=lambda:self.log.delete("1.0","end")).pack(side="left")
        ttk.Button(actions,text="Save log",command=self.save_log).pack(side="left",padx=5)
        ttk.Button(actions,text="Save certificate",command=self.save_cert).pack(side="left")
        ttk.Button(
            actions,
            text="Export certificate report",
            command=self.export_certificate_report).pack(side="left", padx=5)
        ttk.Button(actions,text="Export Message B",command=self.export_message_b_files).pack(side="left",padx=5)
        ttk.Button(actions,text="Verify certificate chain",command=self.verify_certificate_chain).pack(side="left",padx=5)

    def codec(self):
        return FC65Codec(int(self.slave.get(),16),"little",1,self.use_crc.get())

    def refresh_ports(self):
        values=[p.device for p in list_ports.comports()] if list_ports else []
        self.port_combo["values"]=values
        if values and not self.port.get(): self.port.set(values[0])

    def toggle_connect(self):
        if self.ser and self.ser.is_open:
            self.ser.close(); self.ser=None; self.connect_btn.config(text="Connect"); self.status.config(text="Disconnected",foreground="#a00000"); return
        if serial is None:
            messagebox.showerror("Missing package","Install pyserial: python -m pip install pyserial"); return
        try:
            self.ser=serial.Serial(self.port.get(),int(self.baud.get()),bytesize=8,parity=self.parity.get(),stopbits=1,timeout=0.02,write_timeout=1)
            self.connect_btn.config(text="Disconnect");self.status.config(text="Connected",foreground="#087a25");self.write_log(f"OPEN {self.ser.port} {self.ser.baudrate} 8{self.ser.parity}1")
        except Exception as e: messagebox.showerror("COM error",str(e))

    def ver(self): return int(self.version.get(),16)
    def spdm(self,code,p1=0,p2=0,body=b""): return bytes((self.ver(),code,p1&255,p2&255))+body
    def get_version(self):
        self.vca_transcript.clear()
        self.auth_transcript.clear()
        self.cert_transcript_exchanges.clear()
        self.last_challenge_request = b""
        self.connection_authenticated = False
        self.last_measurements_request = b""
        self.last_measurement_context = b""
        self.staged_get_digests_request = b""
        self.measurement_transcript.clear()
        self.pending_large_request = b""
        self.chunk_parent_request = b""
        self.export_message_b = b""
        self.export_logical_certificate = b""
        self.device_certificate = None
        self.device_public_key = None
        self.certificate_format_warning = False
        self.certificate_leaf_link_warning = False
        self.certificate_chain_verified = False
        self.root_ca_match = None
        self.send(bytes((0x10,0x84,0x00,0x00)))
    def get_caps(self):
        version = self.ver()
        data_transfer_size = 255
        if version >= 0x12:
            self.requester_flags = SPDM_CHUNK_CAP if self.advertise_chunk.get() else 0
            max_spdm_msg_size = (
                SPDM_REQUESTER_MAX_SPDM_MSG_SIZE
                if self.requester_flags & SPDM_CHUNK_CAP
                else data_transfer_size
            )
            self.requester_data_transfer_size = data_transfer_size
            self.requester_max_spdm_msg_size = max_spdm_msg_size
            body = (bytes((0, 0, 0, 0)) +
                    struct.pack("<III", self.requester_flags,
                                data_transfer_size, max_spdm_msg_size))
            self.write_log(
                f"  SPDM {version >> 4}.{version & 0x0F} requester capabilities: "
                f"Flags=0x{self.requester_flags:08X}, "
                f"DataTransferSize={data_transfer_size}, "
                f"MaxSPDMmsgSize={max_spdm_msg_size}")
        elif version == 0x11:
            # SPDM 1.1 GET_CAPABILITIES is 12 bytes:
            # Header[4] + CTExponent/Reserved[4] + Flags[4].
            self.requester_flags = SPDM_BASE_REQUESTER_CAPS
            body = bytes((0, 0, 0, 0)) + struct.pack("<I", self.requester_flags)
            self.write_log(
                "  SPDM 1.1 requester capabilities: "
                f"Flags=0x{self.requester_flags:08X} "
                "(CERT_CAP|CHAL_CAP|MEAS_CAP_SIG), wire length=12 bytes; "
                "DataTransferSize/MaxSPDMmsgSize omitted")
        elif version == 0x10:
            # SPDM 1.0 GET_CAPABILITIES is header-only (4 bytes).
            # Capabilities flags are not present in the 1.0 request. Sending the
            # later 1.1 12-byte form makes the GUI and libspdm hash different
            # Message A byte sequences even if the responder accepts the request.
            self.requester_flags = 0
            body = b""
            self.write_log(
                "  SPDM 1.0 requester capabilities: header-only request, "
                "wire length=4 bytes; Flags/DataTransferSize/MaxSPDMmsgSize omitted")
        else:
            raise ValueError(
                f"Unsupported SPDM version 0x{version:02X} for GET_CAPABILITIES")
        self.send(self.spdm(0xE1, 0, 0, body))
    def negotiate(self):
        # BaseAsym ECDSA P-384=0x80; BaseHash SHA-384=0x02.
        body=(struct.pack("<HBBII",32,1,0,0x00000080,0x00000002)
              + bytes(12) + bytes((0,0)) + bytes(2))
        self.send(self.spdm(0xE3,0,0,body))
    def get_digests(self):
        request = self.spdm(0x81)
        if self.pending.request_code:
            self.write_log(
                f"  Intentional extra GET_DIGESTS while RNR pending for "
                f"{SPDM_CODES.get(self.pending.request_code, f'0x{self.pending.request_code:02X}')}; "
                "M1/M2 reset deferred and active transcripts preserved")
        else:
            self.staged_get_digests_request = request
            self.write_log("  GET_DIGESTS staged: Message B resets only after successful DIGESTS")
        self.send(request)
    def get_cert_auto(self):
        requester_chunk = bool(self.requester_flags & SPDM_CHUNK_CAP)
        responder_chunk = bool(self.responder_flags & SPDM_CHUNK_CAP)
        if requester_chunk and responder_chunk:
            self.write_log("  GET_CERTIFICATE AUTO: using CHUNK because Requester CHUNK_CAP is checked and negotiated")
            self.get_cert_via_chunk()
        else:
            reason = "Requester CHUNK_CAP is not checked" if not requester_chunk else "Responder CHUNK_CAP is not advertised"
            self.write_log(f"  GET_CERTIFICATE AUTO: using Offset/Length because {reason}")
            self.get_cert()

    def get_cert(self):
        # Standard GET_CERTIFICATE Offset/Length pagination.
        self.cert_chunk_test = False
        self.cert.clear()
        self.cert_offset = 0
        self._send_cert_chunk()
    def get_cert_via_chunk(self):
        # Ask for the largest certificate portion. If both endpoints negotiated
        # CHUNK_CAP and the responder can build a Large Response, this should
        # initiate the SPDM LargeResponse/CHUNK_GET flow.
        if not (self.requester_flags & SPDM_CHUNK_CAP):
            self.write_log("CHUNK certificate test blocked: send GET_CAPABILITIES with Requester CHUNK_CAP first")
            return
        if not (self.responder_flags & SPDM_CHUNK_CAP):
            self.write_log("CHUNK certificate test blocked: responder did not advertise CHUNK_CAP")
            return
        self.cert_chunk_test = True
        self.cert.clear()
        self.cert_offset = 0
        self.chunk_handle = 0
        self.chunk_seq = 0
        self.chunk_message.clear()
        self.chunk_expected_size = None
        self.chunk_transfer_active = False
        self.chunk_original_request_code = 0x82
        # RC51: Responder debugger confirms Message B retains GET_DIGESTS and
        # DIGESTS, then appends the logical GET_CERTIFICATE and reassembled
        # logical CERTIFICATE. LARGE_RESPONSE and CHUNK wrapper messages are
        # transport control messages and are excluded.
        self.export_logical_certificate = b""
        self.chunk_parent_request = self.spdm(
            0x82, int(self.slot.get()), 0, struct.pack("<HH", 0, 0xFFFF))
        self.write_log(
            "  Message B preserved for CHUNK certificate: retaining GET_DIGESTS + DIGESTS; "
            "staging logical GET_CERTIFICATE")
        self.write_log("Starting certificate CHUNK test: GET_CERTIFICATE Offset=0 Length=0xFFFF")
        self.write_log("  CHUNK transport budget: DataTransferSize=255, one-byte FC65 ByteCount supports up to 255 SPDM bytes")
        self.send(self.chunk_parent_request)
    def _responder_certificate_portion_limit(self) -> int:
        # A normal CERTIFICATE response contains an 8-byte SPDM header before
        # the certificate portion. Keep the complete SPDM response within the
        # responder-advertised DataTransferSize and the one-byte FC65 limit.
        transfer_size = self.responder_data_transfer_size
        if transfer_size is None:
            transfer_size = self.requester_data_transfer_size
        transfer_size = min(transfer_size, SPDM_FC65_MAX_SPDM_PAYLOAD)
        return max(1, transfer_size - SPDM_CERTIFICATE_RESPONSE_HEADER_SIZE)

    def _send_cert_chunk(self, remainder: Optional[int] = None):
        responder_limit = self._responder_certificate_portion_limit()
        configured = max(1, min(responder_limit, int(self.cert_len.get())))
        length = configured if remainder is None else min(configured, remainder)
        body=struct.pack("<HH",self.cert_offset,length)
        self.send(self.spdm(0x82,int(self.slot.get()),0,body))
    def _send_chunk_get(self):
        # SPDM 1.2 CHUNK_GET: Header.Param1=0, Header.Param2=Handle,
        # followed by ChunkSeqNo as uint16 little-endian.
        request=self.spdm(0x86,0,self.chunk_handle,struct.pack("<H",self.chunk_seq))
        self.write_log(f"  Sending CHUNK_GET: handle=0x{self.chunk_handle:02X}, sequence={self.chunk_seq}")
        self.send(request)
    def _finish_chunk_message(self):
        message = bytes(self.chunk_message)
        expected_codes = {
            0x82: 0x02,  # GET_CERTIFICATE -> CERTIFICATE
            0xE0: 0x60,  # GET_MEASUREMENTS -> MEASUREMENTS
        }
        expected_code = expected_codes.get(self.chunk_original_request_code)

        if self.chunk_expected_size is not None and len(message) != self.chunk_expected_size:
            self.write_log(
                f"  CHUNK ERROR: reassembled={len(message)}, "
                f"expected={self.chunk_expected_size}")
            self.chunk_transfer_active = False
            self.cert_chunk_test = False
            return
        self.write_log(f"  CHUNK reassembly complete: {len(message)} bytes")
        if len(message) < 4:
            self.write_log("  CHUNK ERROR: reassembled SPDM message is too short")
            self.chunk_transfer_active = False
            self.cert_chunk_test = False
            return
        if expected_code is not None and message[1] != expected_code:
            self.write_log(
                f"  CHUNK ERROR: logical response code=0x{message[1]:02X}, "
                f"expected=0x{expected_code:02X} for request "
                f"0x{self.chunk_original_request_code:02X}")
            self.write_log("  Reassembled SPDM: " + hx(message))
            self.chunk_transfer_active = False
            self.cert_chunk_test = False
            return

        logical_name = SPDM_CODES.get(message[1], f"CODE_0x{message[1]:02X}")
        self.write_log(
            f"  Reassembled logical SPDM response: {logical_name}, "
            f"total={len(message)} bytes")
        if self.chunk_original_request_code == 0x82:
            self.export_logical_certificate = message
            self.write_log(
                f"  RC56 logical CERTIFICATE export snapshot captured: "
                f"{len(self.export_logical_certificate)} bytes")

        # CHUNK_SEND/GET/RESPONSE wrappers are transport messages. Feed only the
        # reassembled logical response into normal SPDM decoding/transcripts.
        self.chunk_transfer_active = False
        self.cert_chunk_test = False
        completed_request_code = self.chunk_original_request_code
        self.chunk_original_request_code = 0
        self.logical_request_in_flight = 0
        self.chunk_expected_size = None
        self.chunk_message.clear()
        self.write_log(
            f"  CHUNK transaction closed: parent request="
            f"{SPDM_CODES.get(completed_request_code, f'0x{completed_request_code:02X}')}")
        if completed_request_code == 0x82:
            if not self.chunk_parent_request:
                self.write_log("  CHUNK ERROR: staged GET_CERTIFICATE parent is missing")
                self.logical_done.set()
                return
            self.auth_transcript.extend(self.chunk_parent_request)
            self.write_log(
                "  Message B commit: retained GET_DIGESTS + DIGESTS, then logical "
                "GET_CERTIFICATE; ERROR/LARGE_RESPONSE and CHUNK wrappers excluded")
            self.chunk_parent_request = b""
        self.decode(message)
        if completed_request_code == 0x82:
            self.export_message_b = bytes(self.auth_transcript)
            self.write_log(
                f"  RC56 Message B export snapshot captured: "
                f"bytes={len(self.export_message_b)}, "
                f"SHA-384={hashlib.sha384(self.export_message_b).hexdigest().upper()}")
        self.logical_done.set()
    def challenge(self):
        nonce=parse_hex(self.nonce.get())
        if len(nonce)!=32: raise ValueError("Nonce must be exactly 32 bytes")
        # RC39: when another request already owns an RNR token, this CHALLENGE
        # is an intentional probe and must not destroy that deferred transaction.
        # The responder should return BUSY, after which the original RIR remains usable.
        probing_while_pending = bool(self.pending.request_code)
        if probing_while_pending:
            self.write_log(
                f"  Intentional extra CHALLENGE while RNR pending for "
                f"{SPDM_CODES.get(self.pending.request_code, f'0x{self.pending.request_code:02X}')}; "
                f"preserving token=0x{self.pending.token:02X}")
        else:
            self._clear_pending("new CHALLENGE")
        body = nonce
        if self.ver() >= 0x13:
            # DSP0274 1.3 appends RequesterContext[8] to CHALLENGE.
            # A 36-byte SPDM 1.2 CHALLENGE is rejected by libspdm 3.8.2
            # when the negotiated message version is 1.3.
            self.requester_context = os.urandom(8)
            body += self.requester_context
            self.write_log(
                "  SPDM 1.3 CHALLENGE: appended RequesterContext=" +
                hx(self.requester_context))
        request=self.spdm(0x83,int(self.slot.get()),0,body)
        if not probing_while_pending:
            self.last_challenge_request=request
        self.send(request)
    def measurements(self):
        attributes = int(self.meas_attr.get()) & 0xFF
        operation = int(self.meas_index.get()) & 0xFF
        signature_requested = bool(attributes & 0x01)
        body = b""

        # Nonce and SlotIDParam are present only when a signature is requested.
        if signature_requested:
            nonce = parse_hex(self.nonce.get())
            if len(nonce) != 32:
                raise ValueError("Nonce must be exactly 32 bytes")
            body += nonce
            # SlotIDParam was added in SPDM 1.1. SPDM 1.0 carries only Nonce.
            if self.ver() >= 0x11:
                body += bytes((int(self.slot.get()) & 0x0F,))

        # RC56 mirrors libspdm_reset_message_buffer_via_request_code(): a
        # connection-level GET_MEASUREMENTS received before CHALLENGE has
        # authenticated the connection resets Message B/C (M1/M2). A request
        # issued while another RNR transaction is pending is only a BUSY probe
        # and must not change transcript ownership.
        probing_while_pending = bool(self.pending.request_code)
        if not probing_while_pending and not self.connection_authenticated:
            discarded_message_b_size = len(self.auth_transcript)
            self.auth_transcript.clear()
            self.cert_transcript_exchanges.clear()
            self.last_challenge_request = b""
            self.export_message_b = b""
            self.write_log(
                "  SPDM transcript reset: pre-authentication GET_MEASUREMENTS "
                "sets Message B/C=null to match responder state; "
                f"discarded Message B bytes={discarded_message_b_size}")
        wire_measurement_context = b""
        if self.ver() >= 0x13:
            wire_measurement_context = os.urandom(8)
            body += wire_measurement_context
            if not probing_while_pending:
                self.last_measurement_context = wire_measurement_context
                owner = "committed to new measurement transaction"
            else:
                owner = "temporary BUSY-probe context; original context preserved"
            self.write_log(
                f"  SPDM 1.3 GET_MEASUREMENTS: appended Context[8]="
                + hx(wire_measurement_context) + f" ({owner})")
        elif not probing_while_pending:
            self.last_measurement_context = b""

        if signature_requested:
            self.write_log(
                "  Signed GET_MEASUREMENTS: Nonce included"
                + (", SlotIDParam included" if self.ver() >= 0x11 else
                   ", SlotIDParam omitted for SPDM 1.0")
                + (", Context[8] included" if self.ver() >= 0x13 else ""))
        elif self.ver() >= 0x13:
            self.write_log(
                "  Unsigned SPDM 1.3 GET_MEASUREMENTS: "
                "Nonce/SlotIDParam omitted, Context[8] included")
        else:
            self.write_log(
                f"  Unsigned SPDM {self.ver() >> 4}.{self.ver() & 0x0F} "
                "GET_MEASUREMENTS: header-only request; Nonce/SlotIDParam omitted")

        request = self.spdm(0xE0, attributes, operation, body)
        # RC42 keeps both the original request and SPDM 1.3 Context immutable.
        if probing_while_pending:
            self.write_log(
                f"  Intentional extra GET_MEASUREMENTS while RNR pending for "
                f"{SPDM_CODES.get(self.pending.request_code, f'0x{self.pending.request_code:02X}')}; "
                f"preserving token=0x{self.pending.token:02X}, original transcript request, "
                f"and Context[8]={hx(self.last_measurement_context) if self.last_measurement_context else 'N/A'}")
        else:
            self.last_measurements_request = request
        self.write_log(
            f"  GET_MEASUREMENTS wire length={len(request)} bytes, "
            f"operation=0x{operation:02X}, "
            f"signature={'YES' if signature_requested else 'NO'}")
        self.send(request)

    def _cancel_respond_ready_timer(self):
        if self.respond_ready_after_id is not None:
            try:
                self.after_cancel(self.respond_ready_after_id)
            except tk.TclError:
                pass
            self.respond_ready_after_id = None

    def _clear_pending(self, reason=""):
        self._cancel_respond_ready_timer()
        self.pending_generation += 1
        self.pending = PendingRequest(generation=self.pending_generation)
        self.respond_ready_in_flight = False
        if reason:
            self.write_log(f"  Deferred-response state cleared: {reason}")

    def _schedule_respond_ready(self, generation: int):
        self._cancel_respond_ready_timer()
        delay=max(0.0,self.pending.ready_at-time.monotonic())
        delay_ms=max(1,int(delay*1000)+2)
        self.respond_ready_after_id=self.after(
            delay_ms, lambda g=generation:self.respond_ready(g))
        self.write_log(
            f"  Auto RESPOND_IF_READY scheduled in {delay_ms} ms "
            f"for generation={generation}, cycle={self.pending.cycle}")

    def respond_ready(self, expected_generation=None):
        p=self.pending
        if not p.request_code:
            self.write_log("RESPOND_IF_READY skipped: no pending token"); return
        if expected_generation is not None and expected_generation!=p.generation:
            self.write_log(
                f"  Stale RESPOND_IF_READY timer ignored: timer generation="
                f"{expected_generation}, current={p.generation}")
            return
        delay=max(0.0,p.ready_at-time.monotonic())
        if delay:
            self._schedule_respond_ready(p.generation)
            return
        if self.respond_ready_in_flight or self.io_lock.locked():
            self.respond_ready_after_id=self.after(
                20, lambda g=p.generation:self.respond_ready(g))
            return
        self.respond_ready_after_id=None
        self.respond_ready_in_flight=True
        self.write_log(
            f"  Sending RESPOND_IF_READY: request=0x{p.request_code:02X}, "
            f"token=0x{p.token:02X}, cycle={p.cycle}, generation={p.generation}")
        if not self.send(self.spdm(0xFF,p.request_code,p.token)):
            self.respond_ready_in_flight=False

    def send_raw(self):
        try: self.send(parse_hex(self.raw.get("1.0","end")))
        except Exception as e: messagebox.showerror("Invalid SPDM hex",str(e))
    def _record_full_flow_failure(self, reason: str):
        """Record one FULL AUTO failure without stopping independent checks."""
        reason = str(reason).strip() or "unspecified failure"
        if not self.sequence_running:
            return
        if reason not in self.full_flow_failures:
            self.full_flow_failures.append(reason)
            self.write_log(f"  AUTO CHECK FAIL: {reason}")

    def _show_full_auto_result(self, passed: bool, reasons=None):
        """Display the final FULL AUTO result in both the log and a dialog."""
        if passed:
            messagebox.showinfo(
                "FULL AUTO PASS",
                "RC56 FULL AUTO FLOW PASS\n\n"
                "All required protocol, certificate, and signature checks passed.")
            return
        reasons = list(dict.fromkeys(reasons or ["unspecified failure"]))
        details = "\n".join(
            f"{index}. {reason}" for index, reason in enumerate(reasons, 1))
        messagebox.showerror(
            "FULL AUTO FAIL",
            "RC56 FULL AUTO FLOW FAIL\n\nFailure points:\n" + details)

    def run_sequence(self):
        steps=[self.get_version,self.get_caps,self.negotiate,self.get_digests]
        def worker():
            for fn in steps:
                self.request_done.clear(); self.after(0,fn)
                if not self.request_done.wait(float(self.timeout.get())+0.5):
                    self.q.put(("log","Discovery stopped: response timeout")); return
                time.sleep(0.10)
        threading.Thread(target=worker,daemon=True).start()
    def _run_auto_step(self, callback, label, timeout_seconds=None):
        self.logical_done.clear()
        self.q.put(("log", f"AUTO STEP: {label}"))
        self.after(0, callback)
        timeout_seconds = timeout_seconds or (float(self.timeout.get()) + 1.5)
        if not self.logical_done.wait(timeout_seconds):
            reason = f"{label}: timeout after {timeout_seconds:.1f} seconds"
            self.full_flow_failures.append(reason)
            raise TimeoutError(reason)
        time.sleep(0.10)

    def run_full_sequence(self):
        if self.sequence_running:
            self.write_log("AUTO FLOW is already running")
            return
        if not self.ser or not self.ser.is_open:
            messagebox.showwarning("Not connected", "Open a COM port first.")
            return
        self.sequence_running = True
        self.measurement_block_count = None
        self.full_flow_failures = []
        self.full_sequence_btn.config(state="disabled")

        def measurement_request(index, signed):
            def issue():
                self.meas_index.set(index)
                self.meas_attr.set(1 if signed else 0)
                if signed:
                    self.nonce.set(os.urandom(32).hex().upper())
                self.measurements()
            return issue

        def worker():
            try:
                self.q.put(("log", "========== RC56 FULL AUTO FLOW START =========="))
                self._run_auto_step(self.get_version, "GET_VERSION")
                self._run_auto_step(self.get_caps, "GET_CAPABILITIES")
                self._run_auto_step(self.negotiate, "NEGOTIATE_ALGORITHMS")
                self._run_auto_step(self.get_digests, "GET_DIGESTS")
                self._run_auto_step(self.get_cert_auto, "GET_CERTIFICATE AUTO", 20.0)
                # Root CA mismatch is non-terminal. Record failure, then continue
                # CHALLENGE and MEASUREMENTS so all independent checks still run.
                if self.root_ca_match is False:
                    self.full_flow_failures.append("ROOTCA mismatch")
                    self.q.put(("log", "AUTO CHECK FAIL: ROOTCA mismatch; continuing remaining verification"))
                elif self.trusted_root_ca_der is not None and self.root_ca_match is not True:
                    self.full_flow_failures.append("ROOTCA comparison unavailable")
                    self.q.put(("log", "AUTO CHECK FAIL: ROOTCA comparison unavailable; continuing remaining verification"))
                self._run_auto_step(self.challenge, "CHALLENGE signed", 10.0)
                self._run_auto_step(measurement_request(0x00, False), "GET_MEASUREMENTS index 0x00, query block count")
                count = self.measurement_block_count
                self.q.put(("log", f"AUTO: Total measurement blocks reported = {count}"))
                if count is None:
                    raise RuntimeError("Responder did not return a measurement block count")
                if count < 2:
                    raise RuntimeError(
                        f"Responder reports only {count} measurement block(s); "
                        "index 0x02 cannot be requested")
                self._run_auto_step(measurement_request(0x01, False), "GET_MEASUREMENTS index 0x01, unsigned")
                self._run_auto_step(measurement_request(0x02, True), "GET_MEASUREMENTS index 0x02, signed", 10.0)
                self._run_auto_step(measurement_request(0xFF, True), "GET_MEASUREMENTS index 0xFF, all blocks signed", 10.0)
                if self.full_flow_failures:
                    unique_failures = list(dict.fromkeys(self.full_flow_failures))
                    reasons = "; ".join(unique_failures)
                    self.q.put(("log", f"========== RC56 FULL AUTO FLOW FAIL: {reasons} =========="))
                    self.after(0, lambda r=unique_failures: self._show_full_auto_result(False, r))
                else:
                    self.q.put(("log", "========== RC56 FULL AUTO FLOW PASS =========="))
                    self.after(0, lambda: self._show_full_auto_result(True))
            except Exception as exc:
                reason = f"FLOW ABORTED: {type(exc).__name__}: {exc}"
                self.full_flow_failures.append(reason)
                unique_failures = list(dict.fromkeys(self.full_flow_failures))
                reasons = "; ".join(unique_failures)
                self.q.put(("log", f"========== RC56 FULL AUTO FLOW FAIL: {reasons} =========="))
                self.after(0, lambda r=unique_failures: self._show_full_auto_result(False, r))
            finally:
                self.sequence_running = False
                self.after(0, lambda: self.full_sequence_btn.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def send(self,payload):
        request_code = payload[1] if len(payload) >= 2 else 0
        if self.chunk_transfer_active and request_code != 0x86:
            self.write_log(
                f"TX blocked: CHUNK transaction active for logical request "
                f"0x{self.chunk_original_request_code:02X}; only CHUNK_GET is allowed")
            return False
        if not self.ser or not self.ser.is_open:
            messagebox.showwarning("Not connected","Open a COM port first."); return False
        if self.io_lock.locked():
            self.write_log("TX blocked: another FC65 transaction is active"); return False
        try: frame=self.codec().wrap(payload)
        except Exception as e: messagebox.showerror("FC65 configuration error",str(e)); return False
        # RC42: requests sent while an RNR transaction is pending are BUSY
        # probes and must not change VCA, M1/M2, L1/L2, or logical ownership.
        busy_probe = bool(self.pending.request_code) and request_code not in (0xFF, 0x84)
        if request_code == 0xFF and len(payload) >= 3:
            self.logical_request_in_flight = payload[2]
        elif request_code != 0x86 and not busy_probe:
            self.logical_request_in_flight = request_code
        if not busy_probe and len(payload) >= 2 and payload[1] in (0x84, 0xE1, 0xE3):
            self.vca_transcript.extend(payload)
        if not busy_probe and len(payload) >= 2 and payload[1] == 0x82:
            self.pending_large_request = payload
            if self.cert_chunk_test:
                # libspdm logs the logical parent only after LargeResponse
                # reassembly. CHUNK transport control messages are not Message B.
                self.chunk_parent_request = payload
            else:
                self.auth_transcript.extend(payload)
                self.cert_transcript_exchanges.append([payload, None])
        self.last_tx_spdm=payload
        self.write_log(f"TX SPDM {self.describe_spdm(payload)}\n  {hx(payload)}")
        self.write_log(f"TX FC65 ({len(frame)} bytes)\n  {hx(frame)}")
        threading.Thread(target=self._io,args=(frame,),daemon=True).start()
        return True
    def _io(self,frame):
        if not self.io_lock.acquire(blocking=False): return
        try:
            self.ser.reset_input_buffer(); self.ser.write(frame); self.ser.flush()
            deadline=time.monotonic()+float(self.timeout.get()); data=bytearray(); last=None
            while time.monotonic()<deadline:
                n=self.ser.in_waiting
                if n: data+=self.ser.read(n); last=time.monotonic()
                elif data and last and time.monotonic()-last>0.05: break
                time.sleep(0.005)
            self.q.put(("rx",bytes(data)))
        except Exception as e: self.q.put(("err",str(e)))
        finally: self.io_lock.release()
    def _pump(self):
        try:
            while True:
                typ,data=self.q.get_nowait()
                if typ=="err": self.write_log("ERROR "+data)
                elif typ=="log": self.write_log(data)
                else: self.handle_rx(data)
        except queue.Empty: pass
        self.after(50,self._pump)

    def handle_rx(self,frame):
        if not frame:
            self.write_log("RX TIMEOUT / no bytes")
            self._record_full_flow_failure("Transport: RX timeout / no bytes")
            was_rir=self.respond_ready_in_flight
            self.respond_ready_in_flight=False
            if was_rir and self.pending.request_code and self.auto_respond_ready.get():
                self.write_log("  RESPOND_IF_READY transport timeout; retaining latest token and retrying in 100 ms")
                self.respond_ready_after_id=self.after(
                    100, lambda g=self.pending.generation:self.respond_ready(g))
            if self.chunk_transfer_active:
                self.write_log(f"  CHUNK transfer stopped at sequence={self.chunk_seq}, accumulated={len(self.chunk_message)}")
                self.chunk_transfer_active=False
                self.cert_chunk_test=False
                self.chunk_original_request_code=0
                self.logical_request_in_flight=0
                self.chunk_message.clear()
                self.chunk_expected_size=None
            self.request_done.set()
            self.logical_done.set()
            return
        self.respond_ready_in_flight=False
        self.write_log(f"RX FC65 ({len(frame)} bytes)\n  {hx(frame)}")
        try:
            payload,notes=self.codec().unwrap(frame)
            for n in notes:self.write_log("  "+n)
            self.last_rx_spdm=payload; self.write_log(f"RX SPDM {self.describe_spdm(payload)}\n  {hx(payload)}")
            if len(payload) >= 2 and payload[1] in (0x04, 0x61, 0x63):
                self.vca_transcript.extend(payload)
            if len(payload) >= 2 and payload[1] == 0x01:
                request = self.staged_get_digests_request
                if request:
                    self.auth_transcript.clear()
                    self.last_challenge_request = b""
                    self.auth_transcript.extend(request)
                    self.auth_transcript.extend(payload)
                    self.staged_get_digests_request = b""
                    self.write_log("  DIGESTS success: Message B reset and committed")
                else:
                    self.write_log("  DIGESTS WARNING: no staged GET_DIGESTS; transcript unchanged")
            self.decode(payload)
            self.request_done.set()
            if len(payload) >= 2 and payload[1] in (0x04, 0x61, 0x63, 0x01, 0x03, 0x60):
                self.logical_done.set()
            elif len(payload) >= 8 and payload[1] == 0x02:
                remainder = int.from_bytes(payload[6:8], "little")
                if remainder == 0:
                    self.logical_done.set()
        except Exception as e:
            self.write_log("DECODE ERROR: "+str(e))
            self._record_full_flow_failure(f"Decode/transport validation: {type(e).__name__}: {e}")
            self.request_done.set()
            self.logical_done.set()

    def describe_spdm(self,p):
        if len(p)<4:return "<short>"
        return f"v{p[0]>>4}.{p[0]&15} {SPDM_CODES.get(p[1],f'CODE_0x{p[1]:02X}')} Param1=0x{p[2]:02X} Param2=0x{p[3]:02X}"

    def decode(self,p):
        if len(p)<4:return
        code=p[1]
        if code==0x7F:
            err=ERROR_CODES.get(p[2],f"0x{p[2]:02X}"); self.write_log(f"  SPDM ERROR: {err}, ErrorData=0x{p[3]:02X}")
            if p[2] not in (0x0F, 0x42):
                self._record_full_flow_failure(
                    f"SPDM ERROR: {err} (0x{p[2]:02X}), ErrorData=0x{p[3]:02X}")
            if p[2]==0x0F:
                if len(p)<5:
                    self.write_log("  LARGE_RESPONSE ERROR: missing Handle")
                    self.cert_chunk_test=False
                    return
                self.chunk_handle=p[4]
                self.chunk_seq=0
                self.chunk_message.clear()
                self.chunk_expected_size=None
                self.chunk_transfer_active=True
                # RC25: bind the complete CHUNK transaction to the immutable
                # logical request captured when it was sent. Never prefer
                # self.pending here: that RNR record may belong to an earlier
                # GET_MEASUREMENTS and caused CERTIFICATE (0x02) to be checked
                # against MEASUREMENTS (0x60).
                self.chunk_original_request_code = self.logical_request_in_flight
                if self.chunk_original_request_code == 0:
                    self.write_log(
                        "  LARGE_RESPONSE ERROR: logical parent request is unknown; "
                        "CHUNK transfer not started")
                    self.chunk_transfer_active = False
                    self.cert_chunk_test = False
                    return
                origin=SPDM_CODES.get(
                    self.chunk_original_request_code,
                    f"0x{self.chunk_original_request_code:02X}")
                self.write_log(
                    f"  LARGE_RESPONSE received: Handle=0x{self.chunk_handle:02X}, "
                    f"logical request={origin}")
                self.after(100,self._send_chunk_get)
            # RC38: ERROR/Busy means the responder rejected only the extra
            # request because the original asynchronous RNR transaction is still
            # outstanding. Preserve RequestCode, Token, ready_at and generation so
            # the operator can deliberately send another command, observe BUSY,
            # then press RESPOND_IF_READY to complete the original transaction.
            if p[2] == 0x03 and self.pending.request_code:
                pending_name = SPDM_CODES.get(
                    self.pending.request_code,
                    f"0x{self.pending.request_code:02X}")
                self.write_log(
                    f"  BUSY received for extra request; preserving pending "
                    f"{pending_name} token=0x{self.pending.token:02X}, "
                    f"cycle={self.pending.cycle}, generation={self.pending.generation}")
                if self.auto_respond_ready.get():
                    self._schedule_respond_ready(self.pending.generation)
            elif p[2] != 0x42 and self.pending.request_code:
                self._clear_pending(f"terminal SPDM ERROR {err}")
            if p[2]==0x42:
                if len(p)<8:
                    self.write_log(f"  RESPONSE_NOT_READY ERROR: extended data too short ({len(p)} bytes)")
                    return
                # ExtendedErrorData wire order: RDTExponent, RequestCode, Token, RDTM.
                rd_exponent=p[4]
                request_code=p[5]
                token=p[6]
                rdtm=p[7]
                # RC37 compatibility mode: match the legacy libspdm requester.
                # That implementation validates/stores RDTM but computes its wait as:
                #     libspdm_sleep(1ULL << RDTExponent)
                # libspdm_sleep() uses microseconds, so RDTM is intentionally not
                # included in the delay calculation.
                delay_seconds=(2**rd_exponent)/1_000_000.0
                self._cancel_respond_ready_timer()
                self.pending_generation += 1
                self.pending_cycle += 1
                self.pending=PendingRequest(
                    request_code=request_code,
                    token=token,
                    rd_exponent=rd_exponent,
                    rdtm=rdtm,
                    ready_at=time.monotonic()+delay_seconds,
                    generation=self.pending_generation,
                    cycle=self.pending_cycle)
                request_name=SPDM_CODES.get(request_code,f"0x{request_code:02X}")
                self.write_log(
                    f"  RESPONSE_NOT_READY: request={request_name} (0x{request_code:02X}), "
                    f"token=0x{token:02X}, exponent={rd_exponent}, multiplier={rdtm}, "
                    f"delay={delay_seconds*1000:.3f} ms "
                    f"(legacy libspdm compatible: 2^exponent us; RDTM ignored for delay)")
                if self.auto_respond_ready.get():
                    self._schedule_respond_ready(self.pending.generation)
        elif code==0x06:
            # libspdm 3.8.2 SPDM 1.2 CHUNK_RESPONSE:
            # Header(4), ChunkSeqNo(u16), Reserved(u16), ChunkSize(u32),
            # LargeMessageSize(u32, sequence 0 only), then ChunkData.
            if len(p)<12:
                self.write_log(f"  CHUNK ERROR: response too short ({len(p)} bytes)")
                self.cert_chunk_test=False
                return
            attributes=p[2]
            handle=p[3]
            last_chunk=bool(attributes & 0x01)
            sequence=int.from_bytes(p[4:6],"little")
            reserved=int.from_bytes(p[6:8],"little")
            chunk_size=int.from_bytes(p[8:12],"little")
            pos=12
            if handle!=self.chunk_handle:
                self.write_log(f"  CHUNK ERROR: handle=0x{handle:02X}, expected=0x{self.chunk_handle:02X}")
                self.cert_chunk_test=False
                return
            if sequence!=self.chunk_seq:
                self.write_log(f"  CHUNK ERROR: sequence={sequence}, expected={self.chunk_seq}")
                self.cert_chunk_test=False
                return
            if reserved!=0:
                self.write_log(f"  CHUNK WARNING: Reserved=0x{reserved:04X}")
            if sequence==0:
                if len(p)<16:
                    self.write_log("  CHUNK ERROR: first response has no LargeMessageSize")
                    self.cert_chunk_test=False
                    return
                self.chunk_expected_size=int.from_bytes(p[12:16],"little")
                pos=16
                self.write_log(f"  LargeMessageSize={self.chunk_expected_size}")
                if (
                    self.effective_max_spdm_msg_size is not None
                    and self.chunk_expected_size > self.effective_max_spdm_msg_size
                ):
                    self.write_log(
                        "  CHUNK ERROR: LargeMessageSize exceeds responder-derived "
                        f"effective MaxSPDMmsgSize={self.effective_max_spdm_msg_size}")
                    self.chunk_transfer_active = False
                    self.cert_chunk_test = False
                    return
            available=len(p)-pos
            if chunk_size>available:
                self.write_log(f"  CHUNK ERROR: ChunkSize={chunk_size}, available={available}")
                self.cert_chunk_test=False
                return
            chunk=p[pos:pos+chunk_size]
            self.chunk_message.extend(chunk)
            self.write_log(f"  CHUNK_RESPONSE: handle=0x{handle:02X}, sequence={sequence}, chunk={chunk_size}, last={last_chunk}, accumulated={len(self.chunk_message)}")
            if self.chunk_expected_size is not None and len(self.chunk_message)>self.chunk_expected_size:
                self.write_log("  CHUNK ERROR: accumulated data exceeds LargeMessageSize")
                self.cert_chunk_test=False
                return
            if last_chunk:
                self._finish_chunk_message()
            else:
                self.chunk_seq+=1
                self.after(100,self._send_chunk_get)
        elif code==0x04 and len(p)>=6:
            count=p[5]&0x0F; entries=[]
            for i in range(count):
                o=6+i*2
                if o+1<len(p): entries.append(f"{p[o+1]>>4}.{p[o+1]&15}")
            self.write_log("  Supported versions: "+", ".join(entries))
        elif code==0x61:
            if len(p) < 12:
                self.write_log(f"  CAPABILITIES ERROR: response too short ({len(p)} bytes)")
                return
            flags=int.from_bytes(p[8:12],"little")
            self.responder_flags=flags
            if p[0] >= 0x12:
                if len(p) < 20:
                    self.write_log(
                        f"  CAPABILITIES ERROR: SPDM 1.2+ response requires 20 bytes, got {len(p)}")
                    return
                data_size=int.from_bytes(p[12:16],"little")
                max_msg=int.from_bytes(p[16:20],"little")
                self.responder_data_transfer_size = data_size
                self.responder_max_spdm_msg_size = max_msg
                self.effective_data_transfer_size = min(
                    self.requester_data_transfer_size,
                    self.responder_data_transfer_size,
                )
                self.effective_max_spdm_msg_size = min(
                    self.requester_max_spdm_msg_size,
                    self.responder_max_spdm_msg_size,
                )
                negotiated=bool((self.requester_flags & flags) & SPDM_CHUNK_CAP)
                self.write_log(
                    f"  CAPABILITIES: CTExponent={p[4]}, Flags=0x{flags:08X}, "
                    f"DataTransferSize={data_size}, MaxSPDMmsgSize={max_msg}")
                self.write_log(
                    f"  CHUNK_CAP: requester={'YES' if self.requester_flags & SPDM_CHUNK_CAP else 'NO'}, "
                    f"responder={'YES' if flags & SPDM_CHUNK_CAP else 'NO'}, "
                    f"negotiated={'YES' if negotiated else 'NO'}")
                self.write_log(
                    "  Effective post-negotiation limits follow responder capability: "
                    f"DataTransferSize={self.effective_data_transfer_size}, "
                    f"MaxSPDMmsgSize={self.effective_max_spdm_msg_size}")
            else:
                self.write_log(
                    f"  Legacy CAPABILITIES: CTExponent={p[4]}, "
                    f"Flags=0x{flags:08X}, wire length={len(p)} bytes")
                self.write_log("  CHUNK_CAP/DataTransferSize/MaxSPDMmsgSize not present before SPDM 1.2")
        elif code==0x63 and len(p)>=32:
            self.write_log(f"  ALGORITHMS: MeasurementHash=0x{int.from_bytes(p[8:12],'little'):08X}, BaseAsym=0x{int.from_bytes(p[12:16],'little'):08X}, BaseHash=0x{int.from_bytes(p[16:20],'little'):08X}")
        elif code==0x02 and len(p)>=8:
            self.auth_transcript.extend(p)
            if self.cert_transcript_exchanges:
                for exchange in reversed(self.cert_transcript_exchanges):
                    if exchange[1] is None:
                        exchange[1] = bytes(p)
                        break
            if len(p) > SPDM_FC65_MAX_SPDM_PAYLOAD:
                # Responder-confirmed layout for this CHUNK certificate flow:
                #   [0:4]     GET_DIGESTS
                #   [4:56]    DIGESTS
                #   [56:64]   logical GET_CERTIFICATE
                #   [64:end]  reassembled logical CERTIFICATE
                self._log_auth_transcript_diagnostics("CHUNK certificate commit")
            if self.cert_chunk_test:
                self.write_log("  CHUNK test result: responder returned a normal CERTIFICATE response, not LARGE_RESPONSE/CHUNK_RESPONSE")
                self.write_log("  Generic CHUNK was not entered; use the normal certificate button for Offset/Length continuation")
                self.cert_chunk_test = False
            portion=int.from_bytes(p[4:6],"little")
            remainder=int.from_bytes(p[6:8],"little")
            available=max(0,len(p)-8)
            if portion==0:
                self.write_log("  CERTIFICATE ERROR: zero PortionLength; automatic download stopped")
                self._record_full_flow_failure("Certificate response: zero PortionLength")
                return
            if portion>available:
                self.write_log(f"  CERTIFICATE ERROR: PortionLength={portion}, available={available}; automatic download stopped")
                self._record_full_flow_failure(
                    f"Certificate response: PortionLength={portion} exceeds available={available}")
                return
            chunk=p[8:8+portion]
            self.cert.extend(chunk)
            self.cert_offset+=portion
            self.write_log(f"  Certificate portion={portion}, remainder={remainder}, accumulated={len(self.cert)}")
            if remainder:
                # handle_rx runs before _io releases io_lock. Retry shortly from Tk's event loop.
                next_length = min(
                    max(1, min(self._responder_certificate_portion_limit(), int(self.cert_len.get()))),
                    remainder,
                )
                self.write_log(
                    f"  Continuing GET_CERTIFICATE: offset={self.cert_offset}, "
                    f"next length={next_length} (responder-derived limit)")
                self.after(100,lambda r=remainder:self._send_cert_chunk(r))
            else:
                self.write_log(f"  Certificate download complete: {len(self.cert)} bytes")
                if not self.verify_certificate_chain():
                    self._record_full_flow_failure("Certificate: downloaded chain did not pass verification")
        elif code==0x01:
            if p[0] >= 0x13:
                self.write_log(
                    f"  Supported slot mask=0x{p[2]:02X}; "
                    f"provisioned slot mask=0x{p[3]:02X}; "
                    f"digest/metadata bytes={max(0,len(p)-4)}")
            else:
                self.write_log(f"  Slot mask=0x{p[3]:02X}; digest bytes={max(0,len(p)-4)}")
        elif code==0x03:
            self._clear_pending("CHALLENGE_AUTH received")
            self.write_log(f"  CHALLENGE_AUTH received; total={len(p)} bytes")
            challenge_verified = self.verify_challenge_auth_signature(p)
            self.connection_authenticated = bool(challenge_verified)
            self.write_log(
                "  Connection authentication state: " +
                ("AUTHENTICATED" if self.connection_authenticated else
                 "NOT AUTHENTICATED (CHALLENGE_AUTH verification failed)"))
            # DSP0274 Table 38: completion of CHALLENGE sets M1/M2 to null.
            # Preserve Message A / VCA and cached certificate material, but end
            # the current Message B/C transcript so a repeated CHALLENGE uses
            # A2 + B2(null) + a new C1.
            self.auth_transcript.clear()
            self.cert_transcript_exchanges.clear()
            self.last_challenge_request = b""
            self.write_log(
                "  Authentication transcript cleared after CHALLENGE completion: "
                "Message B/C=null; VCA and cached certificate retained")
        elif code==0x60:
            # RC40: a final MEASUREMENTS response completes the deferred RNR/RIR
            # transaction. Clear only the pending token/timer state here. Keep
            # last_measurements_request intact until signature/transcript handling
            # below has consumed the exact original GET_MEASUREMENTS request.
            if self.pending.request_code:
                completed_name = SPDM_CODES.get(
                    self.pending.request_code,
                    f"0x{self.pending.request_code:02X}")
                completed_token = self.pending.token
                self._clear_pending("MEASUREMENTS received")
                self.write_log(
                    f"  Completed deferred {completed_name} transaction; "
                    f"released token=0x{completed_token:02X}")
            self.write_log(f"  MEASUREMENTS received; total={len(p)} bytes")
            if (self.last_measurements_request and
                self.last_measurements_request[3] == 0x00):
                # GET_MEASUREMENTS operation 0x00 asks for the total number of
                # measurement blocks. In MEASUREMENTS, NumberOfBlocks is the
                # SPDM header Param1 byte, therefore payload[2], not payload[4].
                self.measurement_block_count = p[2]
                self.write_log(
                    f"  Measurement total-number response: "
                    f"NumberOfBlocks={self.measurement_block_count} "
                    f"(Header.Param1=0x{p[2]:02X})")
            signed=bool(self.last_measurements_request and
                        (self.last_measurements_request[2] & 0x01))
            if signed:
                self.verify_measurements_signature(p)
            else:
                if not self.last_measurements_request:
                    self.write_log(
                        "  L1/L2 ERROR: original unsigned GET_MEASUREMENTS missing")
                    self._record_full_flow_failure(
                        "MEASUREMENTS: original unsigned GET_MEASUREMENTS request missing")
                    return
                # For an unsigned exchange, append the complete request and
                # response only after the final MEASUREMENTS response arrives.
                # RESPONSE_NOT_READY and RESPOND_IF_READY are not included.
                self.measurement_transcript.extend(self.last_measurements_request)
                self.measurement_transcript.extend(p)
                self.write_log(
                    f"  L1/L2 appended unsigned measurement exchange: "
                    f"index=0x{self.last_measurements_request[3]:02X}, "
                    f"accumulated={len(self.measurement_transcript)} bytes")
                self.last_measurements_request = b""
                self.write_log("  MEASUREMENTS signature: NOT REQUESTED")

    def open_root_ca_der(self):
        path=filedialog.askopenfilename(
            title="Open trusted Root CA DER",
            filetypes=[("DER certificate", "*.der"), ("Certificate files", "*.cer *.crt"), ("All files", "*.*")])
        if not path:
            return
        try:
            data=Path(path).read_bytes()
            if not data:
                raise ValueError("selected file is empty")
            if x509 is None:
                raise RuntimeError("Missing cryptography package. Install: python -m pip install cryptography")
            certificate=x509.load_der_x509_certificate(data)
            # Canonical DER must consume the complete selected file.
            if certificate.public_bytes(serialization.Encoding.DER) != data:
                raise ValueError("file contains trailing data or is not canonical DER")
            self.trusted_root_ca_der=data
            self.trusted_root_ca_path=path
            self.root_ca_match=None
            self.root_ca_path_var.set(path)
            self.write_log(
                f"  Trusted Root CA loaded: {path}, bytes={len(data)}, "
                f"SHA-384={hashlib.sha384(data).hexdigest().upper()}")
            if self.cert:
                self.write_log("  Re-verifying downloaded certificate chain against selected Root CA")
                self.verify_certificate_chain()
        except Exception as e:
            self.trusted_root_ca_der=None
            self.trusted_root_ca_path=""
            self.root_ca_match=None
            self.root_ca_path_var.set("No Root CA selected")
            messagebox.showerror("Root CA DER error", str(e))
            self.write_log("  Trusted Root CA load: FAIL: "+str(e))

    @staticmethod
    def _der_object_size(data: bytes, offset: int) -> int:
        if offset + 2 > len(data) or data[offset] != 0x30:
            raise ValueError("DER certificate SEQUENCE not found")
        first=data[offset+1]
        if first < 0x80:
            return 2 + first
        count=first & 0x7F
        if count == 0 or count > 4 or offset + 2 + count > len(data):
            raise ValueError("Invalid DER length")
        length=int.from_bytes(data[offset+2:offset+2+count],"big")
        return 2 + count + length
    @staticmethod
    def _der_tlv(data: bytes, offset: int = 0):
        if offset + 2 > len(data):
            raise ValueError("Truncated DER TLV")
        tag = data[offset]
        first = data[offset + 1]
        if first < 0x80:
            header = 2
            length = first
        else:
            count = first & 0x7F
            if count == 0 or count > 4 or offset + 2 + count > len(data):
                raise ValueError("Invalid DER length")
            header = 2 + count
            length = int.from_bytes(data[offset + 2:offset + header], "big")
        value_start = offset + header
        end = value_start + length
        if end > len(data):
            raise ValueError("Truncated DER value")
        return tag, value_start, end

    @classmethod
    def _der_children(cls, data: bytes, value_start: int, end: int):
        pos = value_start
        children = []
        while pos < end:
            tag, start, child_end = cls._der_tlv(data, pos)
            children.append((tag, pos, start, child_end))
            pos = child_end
        if pos != end:
            raise ValueError("DER child boundary mismatch")
        return children

    @classmethod
    def _certificate_raw_parts(cls, der: bytes):
        tag, outer_start, outer_end = cls._der_tlv(der, 0)
        if tag != 0x30 or outer_end != len(der):
            raise ValueError("Certificate outer SEQUENCE is invalid")
        outer = cls._der_children(der, outer_start, outer_end)
        if len(outer) != 3 or outer[0][0] != 0x30 or outer[2][0] != 0x03:
            raise ValueError("Certificate requires TBS, AlgorithmIdentifier, Signature")
        _, tbs_tlv, _, tbs_end = outer[0]
        _, _, sig_start, sig_end = outer[2]
        if sig_start >= sig_end or der[sig_start] != 0:
            raise ValueError("Certificate signature BIT STRING is invalid")
        return der[tbs_tlv:tbs_end], der[sig_start + 1:sig_end]

    @classmethod
    def _extract_p384_public_key(cls, der: bytes):
        # Locate the canonical P-384 SubjectPublicKeyInfo algorithm sequence:
        # id-ecPublicKey 1.2.840.10045.2.1 followed by secp384r1 1.3.132.0.34.
        marker = bytes.fromhex("301006072A8648CE3D020106052B81040022")
        pos = der.find(marker)
        if pos < 0:
            raise ValueError("P-384 SubjectPublicKeyInfo algorithm not found")
        bit_pos = pos + len(marker)
        tag, value_start, end = cls._der_tlv(der, bit_pos)
        if tag != 0x03 or value_start >= end or der[value_start] != 0:
            raise ValueError("SubjectPublicKey BIT STRING is invalid")
        point = der[value_start + 1:end]
        if len(point) != 97 or point[0] != 0x04:
            raise ValueError("SubjectPublicKey is not an uncompressed P-384 point")
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP384R1(), point)

    def _load_spdm_certificates(self):
        if x509 is None:
            raise RuntimeError("Missing cryptography package. Install: python -m pip install cryptography")
        chain = bytes(self.cert)
        if len(chain) < 4 + 48 + 4:
            raise ValueError("SPDM certificate chain is incomplete")
        declared = int.from_bytes(chain[0:2], "little")
        if declared and declared != len(chain):
            raise ValueError(f"Certificate chain length mismatch: header={declared}, received={len(chain)}")
        root_hash = chain[4:52]
        pos = 52
        certs = []
        ders = []
        parse_errors = []
        while pos < len(chain):
            size = self._der_object_size(chain, pos)
            if pos + size > len(chain):
                raise ValueError("Truncated DER certificate")
            der = chain[pos:pos + size]
            ders.append(der)
            try:
                certs.append(x509.load_der_x509_certificate(der))
                parse_errors.append(None)
            except Exception as exc:
                certs.append(None)
                parse_errors.append(str(exc))
            pos += size
        if pos != len(chain) or len(ders) < 2:
            raise ValueError(f"Expected root and device certificates; parsed={len(ders)}")
        return root_hash, ders, certs, parse_errors

    def verify_certificate_chain(self):
        self.root_ca_match = None
        self.certificate_format_warning = False
        try:
            root_hash, ders, certs, parse_errors = self._load_spdm_certificates()
            calculated = hashlib.sha384(ders[0]).digest()
            if calculated != root_hash:
                raise ValueError("RootHash SHA-384 mismatch")
            if self.trusted_root_ca_der is None:
                self.write_log("  Root CA file comparison: NOT CHECKED: no trusted .der file selected")
            elif ders[0] == self.trusted_root_ca_der:
                self.root_ca_match = True
                self.write_log("  Root CA file comparison: PASS: downloaded Root CA matches selected DER")
            else:
                self.root_ca_match = False
                self.write_log("  Root CA file comparison: FAIL: ROOTCA mismatch")
                self.write_log("    Downloaded Root CA SHA-384=" + hashlib.sha384(ders[0]).hexdigest().upper())
                self.write_log("    Selected Root CA SHA-384=" + hashlib.sha384(self.trusted_root_ca_der).hexdigest().upper())

            malformed = [i for i, error in enumerate(parse_errors) if error]
            if malformed and not self.ignore_certificate_format_errors.get():
                raise ValueError(
                    "X.509 parse failed for certificate index " +
                    ", ".join(str(i) for i in malformed) + ": " +
                    "; ".join(parse_errors[i] for i in malformed))
            for i in malformed:
                self.certificate_format_warning = True
                self.write_log(
                    f"  Certificate[{i}] X.509 FORMAT WARNING: {parse_errors[i]}")
                self.write_log(
                    "    Compatibility mode: continuing with DER boundaries, "
                    "raw TBSCertificate signature, and P-384 SubjectPublicKeyInfo")

            public_keys = []
            for i, (der, cert) in enumerate(zip(ders, certs)):
                if cert is not None:
                    key = cert.public_key()
                else:
                    key = self._extract_p384_public_key(der)
                if not isinstance(key, ec.EllipticCurvePublicKey):
                    raise ValueError(f"Certificate[{i}] public key is not EC")
                public_keys.append(key)

            # RC56 official-requester compatibility behavior:
            # validate every normally parseable X.509 link strictly. If and only
            # if the final Measurement certificate cannot be parsed because of a
            # malformed extension, still attempt its raw issuer-signature check.
            # A raw mismatch is retained as a diagnostic warning rather than
            # blocking leaf-key use, matching the observed official requester
            # behavior for this MCRPS certificate profile.
            self.certificate_leaf_link_warning = False
            last_index = len(ders) - 1
            for i in range(1, len(ders)):
                issuer_key = public_keys[i - 1]
                child = certs[i]
                verification_mode = "parsed X.509"
                malformed_final_leaf = (
                    i == last_index and
                    child is None and
                    self.ignore_certificate_format_errors.get()
                )
                try:
                    if child is not None:
                        issuer_key.verify(
                            child.signature,
                            child.tbs_certificate_bytes,
                            ec.ECDSA(child.signature_hash_algorithm))
                    else:
                        verification_mode = "raw DER ECDSA-SHA384"
                        tbs_der, signature_der = self._certificate_raw_parts(ders[i])
                        issuer_key.verify(
                            signature_der, tbs_der, ec.ECDSA(hashes.SHA384()))
                    self.write_log(
                        f"  Certificate chain link [{i - 1}] -> [{i}]: "
                        f"PASS ({verification_mode})")
                except InvalidSignature as exc:
                    if malformed_final_leaf:
                        self.certificate_leaf_link_warning = True
                        self.write_log(
                            f"  Certificate chain link [{i - 1}] -> [{i}]: "
                            "WARNING: raw issuer signature did not validate")
                        self.write_log(
                            "    Official-requester compatibility: malformed leaf "
                            "X.509 extensions are ignored; RootHash and all "
                            "parseable CA links remain verified")
                    else:
                        raise ValueError(
                            f"Certificate chain signature mismatch at link "
                            f"[{i - 1}] -> [{i}] ({verification_mode})") from exc
                except Exception as exc:
                    if malformed_final_leaf:
                        self.certificate_leaf_link_warning = True
                        self.write_log(
                            f"  Certificate chain link [{i - 1}] -> [{i}]: "
                            f"WARNING: raw check unavailable: {type(exc).__name__}: {exc}")
                        self.write_log(
                            "    Official-requester compatibility: continuing with "
                            "the extracted Measurement P-384 public key")
                    else:
                        raise ValueError(
                            f"Certificate chain verification error at link "
                            f"[{i - 1}] -> [{i}] ({verification_mode}): "
                            f"{type(exc).__name__}: {exc}") from exc
            leaf_key = public_keys[-1]
            if leaf_key.key_size != 384:
                raise ValueError("Device certificate key is not ECDSA P-384")
            self.device_certificate = certs[-1]
            # RootHash and all parseable CA links must pass before the
            # Measurement key can be promoted. In compatibility mode the only
            # tolerated exception is the malformed final Measurement certificate.
            self.device_public_key = leaf_key
            self.certificate_chain_verified = True
            key_trust = (
                "OFFICIAL-COMPAT" if self.certificate_leaf_link_warning
                else "FULLY-ANCHORED"
            )
            self.write_log(
                "  Measurement certificate P-384 public key accepted for "
                "CHALLENGE_AUTH and signed MEASUREMENTS verification "
                f"(trust={key_trust})")
            format_status = "WARNING/IGNORED" if self.certificate_format_warning else "PASS"
            chain_status = (
                "OFFICIAL-COMPAT/LEAF-WARNING"
                if self.certificate_leaf_link_warning else "PASS"
            )
            root_status = "FAIL" if self.root_ca_match is False else (
                "PASS" if self.root_ca_match is True else "NOT CHECKED")
            self.write_log(
                "  Certificate verification: RootHash=PASS, "
                f"chain signature={chain_status}, device key=ECDSA P-384, "
                f"X.509 format={format_status}, RootCA file={root_status}")
            if self.certificate_format_warning:
                self._record_full_flow_failure("Certificate: malformed X.509 format warning")
            if self.certificate_leaf_link_warning:
                self._record_full_flow_failure("Certificate: final chain-link issuer signature warning")
            if self.root_ca_match is False:
                self._record_full_flow_failure("Certificate: trusted Root CA mismatch")
            return self.root_ca_match is not False
        except Exception as e:
            self.device_certificate = None
            self.device_public_key = None
            self.certificate_leaf_link_warning = False
            self.certificate_chain_verified = False
            reason = f"Certificate verification: {type(e).__name__}: {e}"
            self.write_log("  Certificate verification: FAIL: " + f"{type(e).__name__}: {e}")
            self._record_full_flow_failure(reason)
            return False

    def _log_auth_transcript_diagnostics(self, reason: str):
        message_b = bytes(self.auth_transcript)
        self.write_log(f"  Message B diagnostics ({reason}): bytes={len(message_b)}")
        self.write_log(
            "    Message B SHA-384=" + hashlib.sha384(message_b).hexdigest().upper())
        prefix_size = 56 if len(message_b) >= 56 else len(message_b)
        prefix = message_b[:prefix_size]
        self.write_log(
            f"    B[0:{prefix_size}] GET_DIGESTS + DIGESTS: bytes={len(prefix)}, "
            f"SHA-384={hashlib.sha384(prefix).hexdigest().upper()}")
        complete = 0
        for index, exchange in enumerate(self.cert_transcript_exchanges):
            request, response = exchange
            if response is None:
                self.write_log(f"    Certificate exchange[{index}]: incomplete response")
                continue
            complete += 1
            offset = int.from_bytes(request[4:6], "little") if len(request) >= 8 else -1
            requested = int.from_bytes(request[6:8], "little") if len(request) >= 8 else -1
            portion = int.from_bytes(response[4:6], "little") if len(response) >= 8 else -1
            remainder = int.from_bytes(response[6:8], "little") if len(response) >= 8 else -1
            pair = request + response
            self.write_log(
                f"    Certificate exchange[{index}]: offset={offset}, requested={requested}, "
                f"portion={portion}, remainder={remainder}, pair bytes={len(pair)}, "
                f"SHA-384={hashlib.sha384(pair).hexdigest().upper()}")
        self.write_log(
            f"    Message B certificate exchanges: complete={complete}, "
            f"tracked={len(self.cert_transcript_exchanges)}; every successful wire "
            "GET_CERTIFICATE/CERTIFICATE instance is included in order")
        if message_b:
            self.write_log("    B[0:80]=" + hx(message_b[:80]))
            self.write_log("    B[last 32]=" + hx(message_b[-32:]))
    @staticmethod
    def _spdm_signing_digest(
        spdm_version: int,
        transcript_hash: bytes,
        context: bytes,
    ) -> tuple[bytes, bytes]:
        """Build the version-specific SPDM 1.2 or 1.3 signing digest."""
        if len(transcript_hash) != 48:
            raise ValueError("SHA-384 transcript hash must be 48 bytes")

        if spdm_version == 0x12:
            prefix_unit = b"dmtf-spdm-v1.2.*"
        elif spdm_version == 0x13:
            prefix_unit = b"dmtf-spdm-v1.3.*"
        else:
            raise ValueError(
                f"Unsupported signing-domain version 0x{spdm_version:02X}; "
                "expected SPDM 1.2 or 1.3"
            )

        prefix = prefix_unit * 4
        if len(prefix) != 64:
            raise AssertionError("SPDM signing prefix is not 64 bytes")

        challenge_context = b"responder-challenge_auth signing"
        measurements_context = b"responder-measurements signing"
        if context == challenge_context:
            # Prefix[64] || ZeroPad[4] || ChallengeContext[32] = 100 bytes.
            prefix_context = prefix + (b"\x00" * 4) + context
        elif context == measurements_context:
            # Prefix[64] || ZeroPad[6] || MeasurementsContext[30] = 100 bytes.
            prefix_context = prefix + (b"\x00" * 6) + context
        else:
            raise ValueError("Unsupported SPDM signing context")

        if len(prefix_context) != 100:
            raise AssertionError("SPDM prefix/context field must be 100 bytes")

        signing_input = prefix_context + transcript_hash
        if len(signing_input) != 148:
            raise AssertionError("SPDM SHA-384 signing input must be 148 bytes")

        return hashlib.sha384(signing_input).digest(), prefix_unit
    @staticmethod
    def _raw_p384_to_der(signature_raw: bytes) -> bytes:
        if len(signature_raw) != 96:
            raise ValueError("ECDSA P-384 signature must be 96 bytes")
        r=int.from_bytes(signature_raw[:48],"big")
        ss=int.from_bytes(signature_raw[48:],"big")
        if r == 0 or ss == 0:
            raise ValueError("zero ECDSA component")
        return utils.encode_dss_signature(r,ss)
    def _verify_spdm_signature(
        self,
        label: str,
        spdm_version: int,
        transcript: bytes,
        context: bytes,
        signature_raw: bytes,
    ):
        if not self.certificate_chain_verified and not self.verify_certificate_chain():
            self.write_log(f"  {label} signature: NOT VERIFIED: certificate chain unavailable")
            self._record_full_flow_failure(f"{label}: signature not verified because certificate chain is unavailable")
            return False
        transcript_hash=hashlib.sha384(transcript).digest()
        if spdm_version >= 0x12:
            digest, prefix_unit = self._spdm_signing_digest(
                spdm_version, transcript_hash, context)
            signing_mode = prefix_unit.decode("ascii")
        elif spdm_version in (0x10, 0x11):
            # SPDM 1.0/1.1 signs the legacy transcript hash directly.
            digest = transcript_hash
            prefix_unit = b""
            signing_mode = "legacy transcript SHA-384 (no SPDM 1.2 signing prefix)"
        else:
            raise ValueError(f"Unsupported SPDM version 0x{spdm_version:02X}")
        der_signature=self._raw_p384_to_der(signature_raw)
        try:
            if self.device_public_key is None:
                raise ValueError("Device P-384 public key is unavailable")
            self.device_public_key.verify(
                der_signature,digest,ec.ECDSA(utils.Prehashed(hashes.SHA384())))
            trust_suffix = (
                " (Measurement key accepted in official-requester compatibility mode)"
                if self.certificate_leaf_link_warning else ""
            )
            self.write_log(f"  {label} signature verification: PASS{trust_suffix}")
            result=True
        except InvalidSignature:
            self.write_log(f"  {label} signature verification: FAIL: ECDSA signature mismatch")
            self._record_full_flow_failure(f"{label}: ECDSA signature mismatch")
            result=False
        self.write_log(
            f"    Signing-domain version={spdm_version >> 4}.{spdm_version & 0x0F}"
        )
        self.write_log("    Signing mode=" + signing_mode)
        self.write_log("    Transcript bytes="+str(len(transcript)))
        self.write_log("    Transcript SHA-384="+transcript_hash.hex().upper())
        self.write_log("    Signing digest="+digest.hex().upper())
        self.write_log("    ECDSA R="+signature_raw[:48].hex().upper())
        self.write_log("    ECDSA S="+signature_raw[48:].hex().upper())
        return result
    def verify_challenge_auth_signature(self, response: bytes):
        if not self.last_challenge_request:
            self.write_log("  CHALLENGE_AUTH signature: NOT VERIFIED: original CHALLENGE missing")
            self._record_full_flow_failure("CHALLENGE_AUTH: original CHALLENGE request missing")
            return False
        if len(response) < 96 + 4:
            self.write_log("  CHALLENGE_AUTH signature: FAIL: response too short")
            self._record_full_flow_failure(f"CHALLENGE_AUTH: response too short ({len(response)} bytes)")
            return False
        signature_raw=response[-96:]
        response_without_signature=response[:-96]
        # DSP0274 legacy and SPDM 1.2+ CHALLENGE authentication both use
        # M1/M2 = Concatenate(Message A, Message B, Message C). Message A is
        # the VCA negotiation transcript. Only the signing-domain transform
        # differs for SPDM 1.2+; the underlying transcript still includes VCA.
        challenge_transcript=(bytes(self.auth_transcript)+
                              self.last_challenge_request+response_without_signature)
        transcript=bytes(self.vca_transcript)+challenge_transcript
        self.write_log(
            f"    CHALLENGE transcript: VCA=included, "
            f"VCA bytes={len(self.vca_transcript)}, "
            f"Message B bytes={len(self.auth_transcript)}, "
            f"Message C bytes={len(self.last_challenge_request)+len(response_without_signature)}")
        message_a = bytes(self.vca_transcript)
        message_b = bytes(self.auth_transcript)
        message_c = self.last_challenge_request + response_without_signature
        self.write_log("    Message A SHA-384=" + hashlib.sha384(message_a).hexdigest().upper())
        self.write_log("    Message B SHA-384=" + hashlib.sha384(message_b).hexdigest().upper())
        self.write_log("    Message C SHA-384=" + hashlib.sha384(message_c).hexdigest().upper())
        self._log_auth_transcript_diagnostics("before CHALLENGE_AUTH verification")
        try:
            result = self._verify_spdm_signature(
                "CHALLENGE_AUTH",
                response[0],
                transcript,
                b"responder-challenge_auth signing",
                signature_raw,
            )
            if not result and self.sequence_running:
                self.full_flow_failures.append("CHALLENGE_AUTH signature verification failed")
            return result
        except Exception as e:
            self.write_log("  CHALLENGE_AUTH signature verification: FAIL: "+str(e))
            if self.sequence_running:
                self.full_flow_failures.append("CHALLENGE_AUTH signature verification error")
            return False
    def verify_measurements_signature(self, response: bytes):
        signed=bool(self.last_measurements_request and
                    (self.last_measurements_request[2] & 0x01))
        if not signed:
            self.write_log("  MEASUREMENTS signature: NOT REQUESTED")
            return True
        if len(response) < 96 + 8:
            self.write_log("  MEASUREMENTS signature: FAIL: response too short")
            self._record_full_flow_failure(f"MEASUREMENTS: signed response too short ({len(response)} bytes)")
            return False
        signature_raw=response[-96:]
        response_without_signature=response[:-96]
        # RC33: SPDM 1.0/1.1 legacy L1/L2 excludes Message A/VCA.
        # SPDM 1.2/1.3 prepends VCA. RNR/RIR messages remain excluded.
        measurement_signing_transcript=(bytes(self.measurement_transcript)+
                                        self.last_measurements_request+
                                        response_without_signature)
        if response[0] >= 0x12:
            transcript=bytes(self.vca_transcript)+measurement_signing_transcript
        else:
            transcript=measurement_signing_transcript
        self.write_log(
            f"    L1/L2 VCA="
            f"{'included' if response[0] >= 0x12 else 'omitted for legacy 1.0/1.1'}, "
            f"VCA bytes={len(self.vca_transcript)}, "
            f"prior unsigned bytes={len(self.measurement_transcript)}, "
            f"current request bytes={len(self.last_measurements_request)}, "
            f"current response bytes={len(response_without_signature)}")
        result = False
        try:
            result = self._verify_spdm_signature(
                "MEASUREMENTS",
                response[0],
                transcript,
                b"responder-measurements signing",
                signature_raw,
            )
            if not result:
                self._record_full_flow_failure("MEASUREMENTS: signature verification failed")
        except Exception as e:
            self.write_log("  MEASUREMENTS signature verification: FAIL: "+str(e))
            self._record_full_flow_failure(
                f"MEASUREMENTS: signature verification error: {type(e).__name__}: {e}")
        finally:
            # A signed MEASUREMENTS response completes this L1/L2 sequence.
            self.measurement_transcript.clear()
            self.last_measurements_request = b""
            self.write_log("  L1/L2 cleared after signed MEASUREMENTS completion")
        return result
    def write_log(self,text):
        stamp=datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.insert("end",f"[{stamp}] {text}\n");self.log.see("end")

    def save_log(self):
        p=filedialog.asksaveasfilename(defaultextension=".txt",filetypes=[("Text","*.txt")])
        if p: open(p,"w",encoding="utf-8").write(self.log.get("1.0","end"))
    def save_cert(self):
        p=filedialog.asksaveasfilename(defaultextension=".bin",filetypes=[("Binary","*.bin"),("All","*.*")])
        if p: open(p,"wb").write(self.cert)
    @staticmethod
    def _format_x509_name(name) -> str:
        """Return one readable RFC4514-style distinguished name."""
        try:
            return name.rfc4514_string()
        except Exception:
            return str(name)

    @staticmethod
    def _format_hex_lines(data: bytes, indent: str = "    ", width: int = 16) -> list[str]:
        if not data:
            return [indent + "(empty)"]
        return [
            indent + " ".join(f"{value:02X}" for value in data[offset:offset + width])
            for offset in range(0, len(data), width)
        ]

    @staticmethod
    def _format_extension_value(extension) -> list[str]:
        value = extension.value
        lines = []
        try:
            if isinstance(value, x509.BasicConstraints):
                lines.append(f"CA={value.ca}, path_length={value.path_length}")
            elif isinstance(value, x509.KeyUsage):
                usages = []
                fields = (
                    ("digital_signature", "Digital Signature"),
                    ("content_commitment", "Content Commitment"),
                    ("key_encipherment", "Key Encipherment"),
                    ("data_encipherment", "Data Encipherment"),
                    ("key_agreement", "Key Agreement"),
                    ("key_cert_sign", "Certificate Sign"),
                    ("crl_sign", "CRL Sign"),
                )
                for attribute, label in fields:
                    if getattr(value, attribute):
                        usages.append(label)
                if value.key_agreement:
                    if value.encipher_only:
                        usages.append("Encipher Only")
                    if value.decipher_only:
                        usages.append("Decipher Only")
                lines.append(", ".join(usages) if usages else "(none)")
            elif isinstance(value, x509.SubjectKeyIdentifier):
                lines.append(value.digest.hex().upper())
            elif isinstance(value, x509.AuthorityKeyIdentifier):
                key_identifier = value.key_identifier
                lines.append(
                    "Key Identifier=" +
                    (key_identifier.hex().upper() if key_identifier else "(none)"))
                if value.authority_cert_serial_number is not None:
                    lines.append(
                        "Authority Certificate Serial=" +
                        hex(value.authority_cert_serial_number))
            elif isinstance(value, x509.ExtendedKeyUsage):
                lines.extend(oid.dotted_string for oid in value)
            elif isinstance(value, x509.SubjectAlternativeName):
                lines.extend(str(item) for item in value)
            elif isinstance(value, x509.UnrecognizedExtension):
                lines.append(f"Raw DER ({len(value.value)} bytes):")
                lines.extend(App._format_hex_lines(value.value, indent="  "))
            else:
                rendered = str(value)
                lines.extend(rendered.splitlines() if rendered else ["(empty)"])
        except Exception as exc:
            lines.append(f"Unable to render extension: {type(exc).__name__}: {exc}")
        return lines

    def _build_certificate_chain_report(self) -> str:
        """Decode the current SPDM chain into a human-readable text report."""
        if not self.cert:
            raise ValueError("No SPDM certificate chain is available. Run GET_CERTIFICATE first.")
        if x509 is None or serialization is None or ec is None:
            raise RuntimeError(
                "Missing cryptography package. Install: python -m pip install cryptography")

        chain = bytes(self.cert)
        if len(chain) < 52:
            raise ValueError("SPDM certificate chain is shorter than its 52-byte header")

        declared_length = int.from_bytes(chain[0:2], "little")
        reserved = int.from_bytes(chain[2:4], "little")
        root_hash = chain[4:52]
        position = 52
        certificate_entries = []

        while position < len(chain):
            certificate_size = self._der_object_size(chain, position)
            if position + certificate_size > len(chain):
                raise ValueError(
                    f"Certificate at offset {position} exceeds the SPDM chain boundary")
            der = chain[position:position + certificate_size]
            certificate_entries.append((position, der))
            position += certificate_size

        if position != len(chain):
            raise ValueError("SPDM certificate chain has trailing or incomplete DER data")

        role_names = (
            "Root CA Certificate",
            "Issuing CA Certificate",
            "Device ID Certificate",
            "Measurement Certificate",
        )
        lines = [
            "SPDM CERTIFICATE CHAIN HUMAN-READABLE REPORT",
            "=" * 78,
            f"Generated: {datetime.now().isoformat(sep=' ', timespec='seconds')}",
            f"SPDM chain bytes: {len(chain)}",
            f"Header declared length: {declared_length}",
            f"Header length status: {'MATCH' if declared_length == len(chain) else 'MISMATCH'}",
            f"Header reserved: 0x{reserved:04X}",
            f"Certificate count: {len(certificate_entries)}",
            "RootHash SHA-384: " + root_hash.hex().upper(),
            "Calculated Root DER SHA-384: " + (
                hashlib.sha384(certificate_entries[0][1]).hexdigest().upper()
                if certificate_entries else "N/A"),
            "RootHash status: " + (
                "PASS" if certificate_entries and
                hashlib.sha384(certificate_entries[0][1]).digest() == root_hash
                else "FAIL"),
            "",
        ]

        parsed_certificates = []
        for index, (offset, der) in enumerate(certificate_entries):
            role = role_names[index] if index < len(role_names) else f"Certificate {index + 1}"
            lines.extend([
                "=" * 78,
                f"CERTIFICATE {index + 1}: {role}",
                "=" * 78,
                f"SPDM chain offset: {offset}",
                f"DER length: {len(der)} bytes",
                "DER SHA-384: " + hashlib.sha384(der).hexdigest().upper(),
            ])
            try:
                certificate = x509.load_der_x509_certificate(der)
                parsed_certificates.append(certificate)
                lines.extend([
                    "Parse status: PASS",
                    f"Version: {certificate.version.name}",
                    f"Serial Number: {certificate.serial_number} (0x{certificate.serial_number:X})",
                    "Subject: " + self._format_x509_name(certificate.subject),
                    "Issuer: " + self._format_x509_name(certificate.issuer),
                    "Not Before: " + str(certificate.not_valid_before_utc),
                    "Not After: " + str(certificate.not_valid_after_utc),
                    "Signature Algorithm OID: " + certificate.signature_algorithm_oid.dotted_string,
                    "Signature Hash: " + (
                        certificate.signature_hash_algorithm.name
                        if certificate.signature_hash_algorithm else "N/A"),
                ])

                public_key = certificate.public_key()
                lines.append("Public Key Type: " + type(public_key).__name__)
                lines.append(f"Public Key Size: {getattr(public_key, 'key_size', 'N/A')} bits")
                if isinstance(public_key, ec.EllipticCurvePublicKey):
                    point = public_key.public_bytes(
                        serialization.Encoding.X962,
                        serialization.PublicFormat.UncompressedPoint)
                    lines.append("Elliptic Curve: " + public_key.curve.name)
                    lines.append("Uncompressed EC Point:")
                    lines.extend(self._format_hex_lines(point, indent="  "))

                lines.append(f"Extensions ({len(certificate.extensions)}):")
                for extension in certificate.extensions:
                    oid = extension.oid
                    name = getattr(oid, "_name", None) or "Unknown"
                    lines.append(
                        f"  - {name} ({oid.dotted_string}), critical={extension.critical}")
                    for detail in self._format_extension_value(extension):
                        lines.append("      " + detail)

                lines.append(f"Signature DER ({len(certificate.signature)} bytes):")
                lines.extend(self._format_hex_lines(certificate.signature, indent="  "))
            except Exception as exc:
                parsed_certificates.append(None)
                lines.extend([
                    f"Parse status: FAIL: {type(exc).__name__}: {exc}",
                    "ASN.1/DER raw bytes:",
                ])
                lines.extend(self._format_hex_lines(der, indent="  "))
            lines.append("")

        lines.extend(["=" * 78, "CERTIFICATE CHAIN SIGNATURE CHECKS", "=" * 78])
        for index in range(1, len(certificate_entries)):
            issuer = parsed_certificates[index - 1]
            child = parsed_certificates[index]
            label = f"Certificate {index} -> Certificate {index + 1}"
            if issuer is None or child is None:
                lines.append(f"{label}: NOT CHECKED, certificate parsing failed")
                continue
            try:
                issuer_key = issuer.public_key()
                if not isinstance(issuer_key, ec.EllipticCurvePublicKey):
                    raise ValueError("Issuer key is not elliptic-curve")
                issuer_key.verify(
                    child.signature,
                    child.tbs_certificate_bytes,
                    ec.ECDSA(child.signature_hash_algorithm))
                lines.append(f"{label}: PASS")
            except Exception as exc:
                lines.append(f"{label}: FAIL: {type(exc).__name__}: {exc}")

        lines.append("")
        return "\n".join(lines)

    def export_certificate_report(self):
        """Save a decoded TXT report for the SPDM certificate chain in memory."""
        if not self.cert:
            messagebox.showwarning(
                "Certificate unavailable",
                "Run GET_CERTIFICATE first. No SPDM certificate chain is available.")
            return

        output_path = filedialog.asksaveasfilename(
            title="Export decoded SPDM certificate chain",
            initialfile="spdm_certificate_chain_readable.txt",
            defaultextension=".txt",
            filetypes=[("Text report", "*.txt"), ("All files", "*.*")])
        if not output_path:
            return

        try:
            report = self._build_certificate_chain_report()
            Path(output_path).write_text(report, encoding="utf-8", newline="\n")
            self.write_log(
                f"  Certificate chain readable report exported: {output_path}")
            messagebox.showinfo(
                "Certificate report exported",
                f"Decoded certificate-chain report saved to:\n{output_path}")
        except Exception as exc:
            self.write_log(
                "  Certificate report export: FAIL: "
                f"{type(exc).__name__}: {exc}")
            messagebox.showerror("Certificate report error", str(exc))
    def export_message_b_files(self):
        """Export exact GUI Message B and reassembled logical CERTIFICATE snapshots."""
        message_b = self.export_message_b or bytes(self.auth_transcript)
        if not message_b:
            messagebox.showwarning(
                "Message B unavailable",
                "Run GET_DIGESTS and GET_CERTIFICATE first. No Message B snapshot is available.")
            return
        path = filedialog.asksaveasfilename(
            title="Export GUI Message B",
            initialfile="gui_message_b.bin",
            defaultextension=".bin",
            filetypes=[("Binary", "*.bin"), ("All files", "*.*")])
        if not path:
            return
        try:
            message_b_path = Path(path)
            message_b_path.write_bytes(message_b)
            logical_path = message_b_path.with_name(
                message_b_path.stem + "_logical_certificate.bin")
            if self.export_logical_certificate:
                logical_path.write_bytes(self.export_logical_certificate)
            sha_path = message_b_path.with_name(message_b_path.stem + "_SHA384.txt")
            lines = [
                f"File={message_b_path.name}",
                f"Bytes={len(message_b)}",
                f"SHA384={hashlib.sha384(message_b).hexdigest().upper()}",
            ]
            if self.export_logical_certificate:
                lines.extend([
                    f"LogicalCertificateFile={logical_path.name}",
                    f"LogicalCertificateBytes={len(self.export_logical_certificate)}",
                    "LogicalCertificateSHA384=" +
                    hashlib.sha384(self.export_logical_certificate).hexdigest().upper(),
                ])
            sha_path.write_text("\n".join(lines) + "\n", encoding="ascii")
            self.write_log(
                f"  RC56 Message B exported: {message_b_path}, bytes={len(message_b)}, "
                f"SHA-384={hashlib.sha384(message_b).hexdigest().upper()}")
            if self.export_logical_certificate:
                self.write_log(
                    f"  RC56 logical CERTIFICATE exported: {logical_path}, "
                    f"bytes={len(self.export_logical_certificate)}, "
                    f"SHA-384={hashlib.sha384(self.export_logical_certificate).hexdigest().upper()}")
            self.write_log(f"  RC56 export manifest: {sha_path}")
            messagebox.showinfo(
                "Message B exported",
                f"Message B: {message_b_path}\n"
                f"Bytes: {len(message_b)}\n"
                f"SHA-384: {hashlib.sha384(message_b).hexdigest().upper()}\n\n"
                f"Manifest: {sha_path}")
        except Exception as exc:
            messagebox.showerror("Message B export error", str(exc))
            self.write_log(f"  RC56 Message B export: FAIL: {type(exc).__name__}: {exc}")

    def on_close(self):
        self._cancel_respond_ready_timer()
        if self.ser and self.ser.is_open:self.ser.close()
        self.destroy()

if __name__ == "__main__":
    app=App()
    try: app.mainloop()
    except Exception as e:
        messagebox.showerror("Fatal error",str(e))
