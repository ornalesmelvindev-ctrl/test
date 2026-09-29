#!/usr/bin/env python3
"""
SPDM FC65 Responder Emulator

Transport:
    Modbus-like RTU wrapper:
        Address | FC 0x65 | ByteCount | SPDM payload | CRC16-RTU
    Serial: 115200, 8 data bits, even parity, 1 stop bit

Implemented SPDM responder behavior:
    GET_VERSION             -> VERSION
    GET_CAPABILITIES        -> CAPABILITIES
    NEGOTIATE_ALGORITHMS    -> ALGORITHMS
    GET_DIGESTS             -> DIGESTS
    GET_CERTIFICATE         -> CERTIFICATE or ERROR/LARGE_RESPONSE
    CHUNK_GET               -> CHUNK_RESPONSE

Certificate chain:
    Root CA -> Issuing CA -> Device ID -> Measurement
    Four independent NIST P-384 key pairs are generated at startup.
    Signatures use ECDSA with SHA-384.

Dependencies:
    python -m pip install pyserial cryptography
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import signal
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:
    raise SystemExit(
        "Missing pyserial. Install with: python -m pip install pyserial"
    ) from exc

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from cryptography.x509.oid import NameOID, ObjectIdentifier
except ImportError as exc:
    raise SystemExit(
        "Missing cryptography. Install with: python -m pip install cryptography"
    ) from exc


# -----------------------------------------------------------------------------
# FC65 transport constants
# -----------------------------------------------------------------------------
FC65_FUNCTION_CODE = 0x65
FC65_MAX_BYTE_COUNT = 251
FC65_MAX_FRAME_WITHOUT_ADDRESS = 255
FC65_MAX_WIRE_FRAME = 256

SERIAL_DEFAULT_BAUD_RATE = 115200
SERIAL_DATA_BITS = serial.EIGHTBITS
SERIAL_DEFAULT_PARITY = serial.PARITY_EVEN
SERIAL_STOP_BITS = serial.STOPBITS_ONE

SERIAL_BAUD_RATE_OPTIONS = (
    9600,
    19200,
    38400,
    57600,
    115200,
    230400,
    460800,
    921600,
)

SERIAL_PARITY_OPTIONS = {
    'None': serial.PARITY_NONE,
    'Even': serial.PARITY_EVEN,
    'Odd': serial.PARITY_ODD,
    'Mark': serial.PARITY_MARK,
    'Space': serial.PARITY_SPACE,
}

SERIAL_PARITY_SHORT_NAMES = {
    serial.PARITY_NONE: 'N',
    serial.PARITY_EVEN: 'E',
    serial.PARITY_ODD: 'O',
    serial.PARITY_MARK: 'M',
    serial.PARITY_SPACE: 'S',
}

# -----------------------------------------------------------------------------
# SPDM constants used by the current project
# -----------------------------------------------------------------------------
SPDM_VERSION_10 = 0x10
SPDM_VERSION_12 = 0x12

SPDM_GET_DIGESTS = 0x81
SPDM_GET_CERTIFICATE = 0x82
SPDM_CHUNK_GET = 0x86
SPDM_GET_VERSION = 0x84
SPDM_GET_CAPABILITIES = 0xE1
SPDM_NEGOTIATE_ALGORITHMS = 0xE3

SPDM_DIGESTS = 0x01
SPDM_CERTIFICATE = 0x02
SPDM_VERSION = 0x04
SPDM_CHUNK_RESPONSE = 0x06
SPDM_CAPABILITIES = 0x61
SPDM_ALGORITHMS = 0x63
SPDM_ERROR = 0x7F

SPDM_ERROR_CODE_LARGE_RESPONSE = 0x0F
SPDM_SLOT_ID = 0x00
SPDM_SLOT_MASK = 0x01

SPDM_BASE_HASH_SHA384 = 0x00000002
SPDM_MEASUREMENT_HASH_SHA384 = 0x00000004
SPDM_BASE_ASYM_ECDSA_P384 = 0x00000080

SPDM_RESPONDER_CAPABILITIES = 0x00020016
SPDM_DATA_TRANSFER_SIZE = 251
SPDM_MAX_MESSAGE_SIZE = 3000

# A CHUNK_RESPONSE SPDM payload must stay at or below FC65 ByteCount 251.
# First chunk header after the 4-byte SPDM header is 12 bytes, leaving 235.
# Later chunk header is 8 bytes, leaving 239.
SPDM_FIRST_CHUNK_DATA_SIZE = 235
SPDM_NEXT_CHUNK_DATA_SIZE = 239
SPDM_CHUNK_ATTRIBUTE_LAST = 0x01

CERTIFICATE_CHAIN_HEADER_SIZE = 4
SHA384_SIZE = 48

LOG = logging.getLogger("spdm_fc65_responder")


@dataclass
class CertificateArtifacts:
    root_key: ec.EllipticCurvePrivateKey
    issuing_key: ec.EllipticCurvePrivateKey
    device_key: ec.EllipticCurvePrivateKey
    measurement_key: ec.EllipticCurvePrivateKey
    root_cert: x509.Certificate
    issuing_cert: x509.Certificate
    device_cert: x509.Certificate
    measurement_cert: x509.Certificate
    root_der: bytes
    issuing_der: bytes
    device_der: bytes
    measurement_der: bytes
    chain: bytes


@dataclass
class ChunkTransfer:
    handle: int
    logical_response: bytes
    next_sequence: int = 0
    offset: int = 0


@dataclass
class DeferredSpdmResponse:
    request_code: int
    token: int
    original_request: bytes
    final_response: bytes


# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def hex_bytes(data: bytes, width: int = 16) -> str:
    return "\n".join(
        "  " + " ".join(f"{byte:02X}" for byte in data[index:index + width])
        for index in range(0, len(data), width)
    )


def spdm_header(version: int, code: int, param1: int = 0, param2: int = 0) -> bytes:
    return bytes((version & 0xFF, code & 0xFF, param1 & 0xFF, param2 & 0xFF))


def name(common_name: str, organization: str = "AEI", unit: str = "TW Firmware Team") -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, unit),
        ]
    )


def build_certificate(
    subject: x509.Name,
    issuer: x509.Name,
    public_key,
    signer_key: ec.EllipticCurvePrivateKey,
    serial_number: int,
    is_ca: bool,
    key_cert_sign: bool,
    not_before: dt.datetime,
    not_after: dt.datetime,
    extra_extensions: Optional[list[tuple[x509.ExtensionType, bool]]] = None,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(serial_number)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=is_ca, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=key_cert_sign,
                crl_sign=key_cert_sign,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key),
            critical=False,
        )
    )

    if subject != issuer:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                signer_key.public_key()
            ),
            critical=False,
        )

    for extension, critical in extra_extensions or []:
        builder = builder.add_extension(extension, critical=critical)

    return builder.sign(private_key=signer_key, algorithm=hashes.SHA384())


def generate_certificate_chain(device_id: str) -> CertificateArtifacts:
    root_key = ec.generate_private_key(ec.SECP384R1())
    issuing_key = ec.generate_private_key(ec.SECP384R1())
    device_key = ec.generate_private_key(ec.SECP384R1())
    measurement_key = ec.generate_private_key(ec.SECP384R1())

    not_before = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    not_after = dt.datetime(2045, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc)

    root_name = name("AEI_Testing_Root_Cert", "Advanced Energy Industries, Inc", "ATDC_Firmware_Team")
    issuing_name = name("AEI_Testing_Intermediate_Cert", "Advanced Energy Industries, Inc", "ATDC_Firmware_Team")
    device_name = name(
        f"AEI MCRPS Testing Device ID Cert-{device_id}",
        "AEI",
        "TW Firmware Team",
    )
    measurement_name = name(
        "AEI HPRv2 12kW ITIC Measurement Cert",
        "AEI",
        "ATDC Firmware Team",
    )

    root_cert = build_certificate(
        root_name,
        root_name,
        root_key.public_key(),
        root_key,
        1,
        True,
        True,
        not_before,
        not_after,
    )
    issuing_cert = build_certificate(
        issuing_name,
        root_name,
        issuing_key.public_key(),
        root_key,
        2,
        True,
        True,
        not_before,
        not_after,
    )
    device_cert = build_certificate(
        device_name,
        issuing_name,
        device_key.public_key(),
        issuing_key,
        3,
        True,
        True,
        not_before,
        not_after,
    )

    zero_hash = bytes(48)
    measurement_extensions = [
        (
            x509.UnrecognizedExtension(
                ObjectIdentifier(f"1.2.3.{index}"),
                bytes((0x00,)) + zero_hash,
            ),
            False,
        )
        for index in range(1, 5)
    ]
    measurement_cert = build_certificate(
        measurement_name,
        device_name,
        measurement_key.public_key(),
        device_key,
        1,
        False,
        False,
        not_before,
        not_after,
        measurement_extensions,
    )

    root_der = root_cert.public_bytes(serialization.Encoding.DER)
    issuing_der = issuing_cert.public_bytes(serialization.Encoding.DER)
    device_der = device_cert.public_bytes(serialization.Encoding.DER)
    measurement_der = measurement_cert.public_bytes(serialization.Encoding.DER)

    chain_body = root_der + issuing_der + device_der + measurement_der
    root_hash = hashlib.sha384(root_der).digest()
    chain_length = CERTIFICATE_CHAIN_HEADER_SIZE + len(root_hash) + len(chain_body)
    if chain_length > 0xFFFF:
        raise ValueError("Certificate chain exceeds 16-bit SPDM chain length")

    chain = struct.pack("<HH", chain_length, 0) + root_hash + chain_body

    return CertificateArtifacts(
        root_key=root_key,
        issuing_key=issuing_key,
        device_key=device_key,
        measurement_key=measurement_key,
        root_cert=root_cert,
        issuing_cert=issuing_cert,
        device_cert=device_cert,
        measurement_cert=measurement_cert,
        root_der=root_der,
        issuing_der=issuing_der,
        device_der=device_der,
        measurement_der=measurement_der,
        chain=chain,
    )


def write_artifacts(output_dir: Path, artifacts: CertificateArtifacts) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    certs = [
        ("root", artifacts.root_cert, artifacts.root_key),
        ("issuing_ca", artifacts.issuing_cert, artifacts.issuing_key),
        ("device_id", artifacts.device_cert, artifacts.device_key),
        ("measurement", artifacts.measurement_cert, artifacts.measurement_key),
    ]
    for stem, cert, key in certs:
        (output_dir / f"{stem}.der").write_bytes(
            cert.public_bytes(serialization.Encoding.DER)
        )
        (output_dir / f"{stem}.pem").write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        (output_dir / f"{stem}_private_key.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    (output_dir / "spdm_certificate_chain.bin").write_bytes(artifacts.chain)

    report_lines = [
        "SPDM FC65 Responder Emulator Certificate Report",
        "",
        f"SPDM chain bytes: {len(artifacts.chain)}",
        f"RootHash SHA-384: {hashlib.sha384(artifacts.root_der).hexdigest()}",
        "",
    ]
    for index, (stem, cert, _) in enumerate(certs, start=1):
        report_lines.extend(
            [
                f"Certificate {index}: {stem}",
                f"  Subject: {cert.subject.rfc4514_string()}",
                f"  Issuer: {cert.issuer.rfc4514_string()}",
                f"  Serial: {cert.serial_number}",
                f"  Signature: {cert.signature_algorithm_oid.dotted_string}",
                f"  SHA-384: {cert.fingerprint(hashes.SHA384()).hex()}",
                "",
            ]
        )
    (output_dir / "certificate_report.txt").write_text(
        "\n".join(report_lines), encoding="utf-8"
    )


ARTIFACT_FILE_NAMES = {
    'root_key': 'root_private_key.pem',
    'issuing_key': 'issuing_ca_private_key.pem',
    'device_key': 'device_id_private_key.pem',
    'measurement_key': 'measurement_private_key.pem',
    'root_cert': 'root.der',
    'issuing_cert': 'issuing_ca.der',
    'device_cert': 'device_id.der',
    'measurement_cert': 'measurement.der',
    'chain': 'spdm_certificate_chain.bin',
}


def artifact_files_exist(output_dir: Path) -> bool:
    """Return True only when the complete reusable artifact set exists."""
    return all((output_dir / file_name).is_file()
               for file_name in ARTIFACT_FILE_NAMES.values())


def _public_key_bytes(public_key) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _verify_certificate_signature(
    certificate: x509.Certificate,
    issuer_public_key,
) -> None:
    issuer_public_key.verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        ec.ECDSA(certificate.signature_hash_algorithm),
    )


def load_artifacts(output_dir: Path) -> CertificateArtifacts:
    """Load and validate an existing four-certificate emulator identity.

    No files are modified. The function rejects incomplete, mismatched, or
    damaged key/certificate sets instead of silently regenerating identity.
    """
    missing = [
        file_name
        for file_name in ARTIFACT_FILE_NAMES.values()
        if not (output_dir / file_name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            'Artifact directory is incomplete. Missing: ' + ', '.join(missing)
        )

    def load_key(file_name: str) -> ec.EllipticCurvePrivateKey:
        key = serialization.load_pem_private_key(
            (output_dir / file_name).read_bytes(),
            password=None,
        )
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError(f'{file_name} is not an EC private key')
        if not isinstance(key.curve, ec.SECP384R1):
            raise ValueError(f'{file_name} is not a NIST P-384 private key')
        return key

    root_key = load_key(ARTIFACT_FILE_NAMES['root_key'])
    issuing_key = load_key(ARTIFACT_FILE_NAMES['issuing_key'])
    device_key = load_key(ARTIFACT_FILE_NAMES['device_key'])
    measurement_key = load_key(ARTIFACT_FILE_NAMES['measurement_key'])

    root_der = (output_dir / ARTIFACT_FILE_NAMES['root_cert']).read_bytes()
    issuing_der = (output_dir / ARTIFACT_FILE_NAMES['issuing_cert']).read_bytes()
    device_der = (output_dir / ARTIFACT_FILE_NAMES['device_cert']).read_bytes()
    measurement_der = (output_dir / ARTIFACT_FILE_NAMES['measurement_cert']).read_bytes()

    root_cert = x509.load_der_x509_certificate(root_der)
    issuing_cert = x509.load_der_x509_certificate(issuing_der)
    device_cert = x509.load_der_x509_certificate(device_der)
    measurement_cert = x509.load_der_x509_certificate(measurement_der)

    pairs = (
        ('root', root_key, root_cert),
        ('issuing_ca', issuing_key, issuing_cert),
        ('device_id', device_key, device_cert),
        ('measurement', measurement_key, measurement_cert),
    )
    for stem, key, certificate in pairs:
        if _public_key_bytes(key.public_key()) != _public_key_bytes(
                certificate.public_key()):
            raise ValueError(
                f'{stem} private key does not match its certificate public key'
            )

    if root_cert.issuer != root_cert.subject:
        raise ValueError('Root certificate is not self-issued')
    if issuing_cert.issuer != root_cert.subject:
        raise ValueError('Issuing CA issuer does not match Root subject')
    if device_cert.issuer != issuing_cert.subject:
        raise ValueError('Device ID issuer does not match Issuing CA subject')
    if measurement_cert.issuer != device_cert.subject:
        raise ValueError('Measurement issuer does not match Device ID subject')

    _verify_certificate_signature(root_cert, root_cert.public_key())
    _verify_certificate_signature(issuing_cert, root_cert.public_key())
    _verify_certificate_signature(device_cert, issuing_cert.public_key())
    _verify_certificate_signature(measurement_cert, device_cert.public_key())

    chain = (output_dir / ARTIFACT_FILE_NAMES['chain']).read_bytes()
    expected_body = root_der + issuing_der + device_der + measurement_der
    expected_length = CERTIFICATE_CHAIN_HEADER_SIZE + SHA384_SIZE + len(expected_body)
    if len(chain) != expected_length:
        raise ValueError(
            f'SPDM chain size mismatch: file={len(chain)}, expected={expected_length}'
        )
    declared_length, reserved = struct.unpack_from('<HH', chain, 0)
    if declared_length != len(chain) or reserved != 0:
        raise ValueError('SPDM chain header length/reserved field is invalid')
    if chain[4:4 + SHA384_SIZE] != hashlib.sha384(root_der).digest():
        raise ValueError('SPDM chain RootHash does not match root.der')
    if chain[4 + SHA384_SIZE:] != expected_body:
        raise ValueError('SPDM chain certificate bytes do not match DER files')

    return CertificateArtifacts(
        root_key=root_key,
        issuing_key=issuing_key,
        device_key=device_key,
        measurement_key=measurement_key,
        root_cert=root_cert,
        issuing_cert=issuing_cert,
        device_cert=device_cert,
        measurement_cert=measurement_cert,
        root_der=root_der,
        issuing_der=issuing_der,
        device_der=device_der,
        measurement_der=measurement_der,
        chain=chain,
    )


# -----------------------------------------------------------------------------
# Responder
# -----------------------------------------------------------------------------
class SpdmFc65Responder:
    def __init__(self, slave_address: int, artifacts: CertificateArtifacts):
        self.slave_address = slave_address
        self.artifacts = artifacts
        self.chunk_transfer: Optional[ChunkTransfer] = None
        self.next_handle = 1

    def build_fc65_frame(self, spdm_payload: bytes) -> bytes:
        if not 1 <= len(spdm_payload) <= FC65_MAX_BYTE_COUNT:
            raise ValueError(f"FC65 payload length invalid: {len(spdm_payload)}")
        body = bytes((self.slave_address, FC65_FUNCTION_CODE, len(spdm_payload))) + spdm_payload
        crc = crc16_modbus(body)
        frame = body + struct.pack("<H", crc)
        if len(frame) > FC65_MAX_WIRE_FRAME:
            raise ValueError(f"FC65 wire frame too large: {len(frame)}")
        return frame

    def handle(self, request: bytes) -> bytes:
        if len(request) < 4:
            return self.error_response(SPDM_VERSION_12, 0x01, 0)

        version, code, param1, param2 = request[:4]
        LOG.info(
            "RX SPDM v%d.%d code=0x%02X Param1=0x%02X Param2=0x%02X (%d bytes)",
            version >> 4,
            version & 0x0F,
            code,
            param1,
            param2,
            len(request),
        )

        if code == SPDM_GET_VERSION:
            return self.get_version()
        if code == SPDM_GET_CAPABILITIES:
            return self.get_capabilities(version)
        if code == SPDM_NEGOTIATE_ALGORITHMS:
            return self.negotiate_algorithms(version)
        if code == SPDM_GET_DIGESTS:
            return self.get_digests(version)
        if code == SPDM_GET_CERTIFICATE:
            return self.get_certificate(version, request)
        if code == SPDM_CHUNK_GET:
            return self.chunk_get(version, request)

        LOG.warning("Unsupported SPDM request code 0x%02X", code)
        return self.error_response(version, 0x07, code)

    @staticmethod
    def get_version() -> bytes:
        versions = [0x1000, 0x1100, 0x1200, 0x1300]
        payload = bytearray(spdm_header(SPDM_VERSION_10, SPDM_VERSION))
        payload.extend((0x00, len(versions)))
        for version in versions:
            payload.extend(struct.pack("<H", version))
        return bytes(payload)

    @staticmethod
    def get_capabilities(version: int) -> bytes:
        # Matches the 20-byte response shape in the project log.
        return (
            spdm_header(version, SPDM_CAPABILITIES)
            + bytes((0x00, 0x00, 0x00, 0x00))
            + bytes((0x16, 0x00, 0x02, 0x00))
            + struct.pack("<I", SPDM_DATA_TRANSFER_SIZE)
            + struct.pack("<I", SPDM_MAX_MESSAGE_SIZE)
        )

    @staticmethod
    def negotiate_algorithms(version: int) -> bytes:
        # 36-byte ALGORITHMS response matching the current responder profile.
        payload = bytearray(spdm_header(version, SPDM_ALGORITHMS))
        payload.extend(struct.pack("<H", 36))
        payload.extend(bytes((0x01, 0x00)))
        payload.extend(struct.pack("<I", SPDM_MEASUREMENT_HASH_SHA384))
        payload.extend(struct.pack("<I", SPDM_BASE_ASYM_ECDSA_P384))
        payload.extend(struct.pack("<I", SPDM_BASE_HASH_SHA384))
        payload.extend(bytes(16))
        return bytes(payload)

    def get_digests(self, version: int) -> bytes:
        digest = hashlib.sha384(self.artifacts.chain).digest()
        return spdm_header(version, SPDM_DIGESTS, 0x00, SPDM_SLOT_MASK) + digest

    def get_certificate(self, version: int, request: bytes) -> bytes:
        if len(request) < 8:
            return self.error_response(version, 0x01, 0)
        slot_id = request[2] & 0x0F
        offset, requested_length = struct.unpack_from("<HH", request, 4)
        if slot_id != SPDM_SLOT_ID or offset > len(self.artifacts.chain):
            return self.error_response(version, 0x01, 0)

        if requested_length == 0xFFFF:
            requested_length = len(self.artifacts.chain) - offset
        portion = self.artifacts.chain[offset:offset + requested_length]
        remainder = len(self.artifacts.chain) - offset - len(portion)
        logical_response = (
            spdm_header(version, SPDM_CERTIFICATE, slot_id, 0)
            + struct.pack("<HH", len(portion), remainder)
            + portion
        )

        # CHUNK transport wrappers are excluded from Message B. Commit the
        # original logical GET_CERTIFICATE and logical CERTIFICATE exactly once.
        self.transcript_b.extend(request)
        self.transcript_b.extend(logical_response)

        if len(logical_response) <= FC65_MAX_BYTE_COUNT:
            return logical_response

        handle = self.next_handle & 0xFF
        if handle == 0:
            handle = 1
        self.next_handle = handle + 1
        self.chunk_transfer = ChunkTransfer(handle=handle, logical_response=logical_response)
        LOG.info(
            "Large response staged: handle=0x%02X, logical bytes=%d",
            handle,
            len(logical_response),
        )
        return self.error_response(version, SPDM_ERROR_CODE_LARGE_RESPONSE, handle)

    def chunk_get(self, version: int, request: bytes) -> bytes:
        if len(request) < 6 or self.chunk_transfer is None:
            return self.error_response(version, 0x01, 0)

        handle = request[3]
        sequence = struct.unpack_from("<H", request, 4)[0]
        transfer = self.chunk_transfer
        if handle != transfer.handle or sequence != transfer.next_sequence:
            LOG.warning(
                "Invalid CHUNK_GET handle/sequence: got %02X/%d expected %02X/%d",
                handle,
                sequence,
                transfer.handle,
                transfer.next_sequence,
            )
            return self.error_response(version, 0x01, 0)

        first = sequence == 0
        capacity = SPDM_FIRST_CHUNK_DATA_SIZE if first else SPDM_NEXT_CHUNK_DATA_SIZE
        chunk = transfer.logical_response[transfer.offset:transfer.offset + capacity]
        transfer.offset += len(chunk)
        last = transfer.offset >= len(transfer.logical_response)
        attributes = SPDM_CHUNK_ATTRIBUTE_LAST if last else 0

        response = bytearray(
            spdm_header(version, SPDM_CHUNK_RESPONSE, attributes, handle)
        )
        response.extend(struct.pack("<H", sequence))
        response.extend(struct.pack("<H", 0))
        response.extend(struct.pack("<I", len(chunk)))
        if first:
            response.extend(struct.pack("<I", len(transfer.logical_response)))
        response.extend(chunk)

        LOG.info(
            "TX CHUNK_RESPONSE handle=0x%02X seq=%d chunk=%d last=%s offset=%d/%d",
            handle,
            sequence,
            len(chunk),
            last,
            transfer.offset,
            len(transfer.logical_response),
        )
        transfer.next_sequence += 1
        if last:
            self.chunk_transfer = None
        return bytes(response)

    @staticmethod
    def error_response(version: int, error_code: int, error_data: int) -> bytes:
        return spdm_header(version, SPDM_ERROR, error_code, 0) + bytes((error_data,))


class SerialFrameReader:
    def __init__(self, port: serial.Serial, slave_address: int):
        self.port = port
        self.slave_address = slave_address
        self.buffer = bytearray()

    def read_frame(self) -> Optional[bytes]:
        incoming = self.port.read(self.port.in_waiting or 1)
        if incoming:
            self.buffer.extend(incoming)

        while len(self.buffer) >= 3:
            if self.buffer[0] != self.slave_address or self.buffer[1] != FC65_FUNCTION_CODE:
                del self.buffer[0]
                continue

            byte_count = self.buffer[2]
            if byte_count == 0 or byte_count > FC65_MAX_BYTE_COUNT:
                LOG.warning("Dropping invalid FC65 ByteCount=%d", byte_count)
                del self.buffer[0]
                continue

            frame_length = 3 + byte_count + 2
            if len(self.buffer) < frame_length:
                return None

            frame = bytes(self.buffer[:frame_length])
            del self.buffer[:frame_length]
            received_crc = struct.unpack_from("<H", frame, frame_length - 2)[0]
            calculated_crc = crc16_modbus(frame[:-2])
            if received_crc != calculated_crc:
                LOG.warning(
                    "Dropping bad CRC frame: received=0x%04X calculated=0x%04X",
                    received_crc,
                    calculated_crc,
                )
                continue
            return frame
        return None


def parse_int(value: str) -> int:
    return int(value, 0)


def cli_main() -> int:
    parser = argparse.ArgumentParser(description="SPDM responder over FC65 serial transport")
    parser.add_argument("--port", required=True, help="COM port, for example COM16")
    parser.add_argument("--address", type=parse_int, default=0x80, help="Slave address, default 0x80")
    parser.add_argument("--device-id", default="STM-1e0041000e50314351383320")
    parser.add_argument("--output-dir", type=Path, default=Path("spdm_emulator_artifacts"))
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING"), default="INFO")
    args = parser.parse_args()

    if not 1 <= args.address <= 0xF7:
        parser.error("--address must be from 1 through 247")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(asctime)s.%(msecs)03d] %(message)s",
        datefmt="%H:%M:%S",
    )

    LOG.info("Generating four NIST P-384 key pairs and certificates")
    artifacts = generate_certificate_chain(args.device_id)
    write_artifacts(args.output_dir, artifacts)
    LOG.info("Certificate chain generated: %d bytes", len(artifacts.chain))
    LOG.info("Artifacts written to: %s", args.output_dir.resolve())

    responder = SpdmFc65Responder(args.address, artifacts)
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    with serial.Serial(
        port=args.port,
        baudrate=SERIAL_DEFAULT_BAUD_RATE,
        bytesize=SERIAL_DATA_BITS,
        parity=SERIAL_DEFAULT_PARITY,
        stopbits=SERIAL_STOP_BITS,
        timeout=0.05,
        write_timeout=1.0,
    ) as port:
        port.reset_input_buffer()
        port.reset_output_buffer()
        reader = SerialFrameReader(port, args.address)
        LOG.info("OPEN %s 115200 8E1, address=0x%02X, FC=0x65", args.port, args.address)

        while not stop_requested:
            frame = reader.read_frame()
            if frame is None:
                time.sleep(0.001)
                continue

            LOG.debug("RX FC65 (%d bytes)\n%s", len(frame), hex_bytes(frame))
            spdm_request = frame[3:-2]
            try:
                spdm_response = responder.handle(spdm_request)
                response_frame = responder.build_fc65_frame(spdm_response)
            except Exception:
                LOG.exception("Failed to process SPDM request")
                continue

            port.write(response_frame)
            port.flush()
            LOG.debug("TX FC65 (%d bytes)\n%s", len(response_frame), hex_bytes(response_frame))

    LOG.info("Responder stopped")
    return 0


# =============================================================================
# GUI, CHALLENGE, GET_MEASUREMENTS, and DRY RUN extension
# =============================================================================
import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

SPDM_CHALLENGE = 0x83
SPDM_CHALLENGE_AUTH = 0x03
SPDM_RESPOND_IF_READY = 0xFF
SPDM_ERROR_CODE_RESPONSE_NOT_READY = 0x42
SPDM_RNR_RDT_EXPONENT = 19
SPDM_RNR_RDTM = 2
SPDM_GET_MEASUREMENTS = 0xE0
SPDM_MEASUREMENTS = 0x60
SPDM_GET_MEASUREMENTS_REQUEST_ATTRIBUTES_GENERATE_SIGNATURE = 0x01
SPDM_NONCE_SIZE = 32
SPDM_P384_SIGNATURE_SIZE = 96
SPDM_MEASUREMENT_OPERATION_TOTAL_NUMBER = 0x00
SPDM_MEASUREMENT_OPERATION_ALL = 0xFF
SPDM_MEASUREMENT_SPECIFICATION_DMTF = 0x01
SPDM_MEASUREMENT_BLOCK_MEASUREMENT = 0x00
SPDM_MEASUREMENT_BLOCK_MANIFEST = 0x01
SPDM_MEASUREMENT_VALUE_VERSION = 0x00
SPDM_MEASUREMENT_VALUE_IMMUTABLE_ROM = 0x01
# SPDM 1.2 MEASUREMENTS Header.Param2 ContentChanged=NOT_SUPPORTED and SlotID=0.
SPDM_MEASUREMENTS_PARAM2_CONTENT_CHANGED_NOT_SUPPORTED = 0x00
SPDM_DRYRUN_REQUEST_NONCE = bytes(range(32))


def ecdsa_der_to_raw_p384(signature_der: bytes) -> bytes:
    r, s = decode_dss_signature(signature_der)
    return r.to_bytes(48, 'big') + s.to_bytes(48, 'big')


def ecdsa_raw_to_der_p384(signature_raw: bytes) -> bytes:
    if len(signature_raw) != SPDM_P384_SIGNATURE_SIZE:
        raise ValueError('P-384 raw signature must be 96 bytes')
    r = int.from_bytes(signature_raw[:48], 'big')
    s = int.from_bytes(signature_raw[48:], 'big')
    return encode_dss_signature(r, s)


class EnhancedSpdmFc65Responder(SpdmFc65Responder):
    """Extends the certificate responder with authentication and measurements.

    The emulator maintains a deterministic request/response transcript for its
    self-test. Signatures are raw P-384 R || S and use ECDSA with SHA-384.
    """

    def __init__(self, slave_address: int, artifacts: CertificateArtifacts):
        super().__init__(slave_address, artifacts)
        self.transcript_a = bytearray()
        self.transcript_b = bytearray()
        self.measurement_transcript = bytearray()
        self.last_signature_input = b''
        self.last_signature_raw = b''
        self.measurement_records = self._build_measurement_records()
        self.deferred_response: Optional[DeferredSpdmResponse] = None
        self.next_rnr_token = 1

    def reset_protocol_state(self, reason: str = 'explicit reset') -> None:
        """Reset all state that belongs to the current SPDM connection.

        GET_VERSION starts a new SPDM connection negotiation. Certificate and
        private-key artifacts remain unchanged, while every transcript,
        deferred large-response transfer, and cached signature is discarded.
        """
        transcript_a_bytes = len(self.transcript_a)
        transcript_b_bytes = len(self.transcript_b)
        measurement_bytes = len(self.measurement_transcript)
        had_chunk_transfer = self.chunk_transfer is not None

        self.transcript_a.clear()
        self.transcript_b.clear()
        self.measurement_transcript.clear()
        self.chunk_transfer = None
        self.next_handle = 1
        self.last_signature_input = b''
        self.last_signature_raw = b''
        self.deferred_response = None
        self.next_rnr_token = 1
        LOG.info(
            'SPDM connection state reset: reason=%s, MessageA=%d, '
            'MessageB=%d, L1L2=%d, chunk=%s',
            reason,
            transcript_a_bytes,
            transcript_b_bytes,
            measurement_bytes,
            had_chunk_transfer,
        )

    @staticmethod
    def _build_measurement_block(index: int, value_type: int, digest: bytes) -> bytes:
        measurement = bytes((value_type & 0x7F,)) + struct.pack('<H', len(digest)) + digest
        return (
            bytes((index, SPDM_MEASUREMENT_SPECIFICATION_DMTF))
            + struct.pack('<H', len(measurement))
            + measurement
        )

    def _build_measurement_records(self) -> list[bytes]:
        firmware_digest = hashlib.sha384(b'AEI HPRv2 12kW ITIC emulator firmware').digest()
        config_digest = hashlib.sha384(b'AEI HPRv2 12kW ITIC emulator configuration').digest()
        return [
            self._build_measurement_block(1, SPDM_MEASUREMENT_VALUE_IMMUTABLE_ROM, firmware_digest),
            self._build_measurement_block(2, SPDM_MEASUREMENT_VALUE_VERSION, config_digest),
        ]

    @staticmethod
    def _spdm_signing_digest(version: int, transcript: bytes, context: bytes) -> tuple[bytes, bytes]:
        transcript_hash = hashlib.sha384(transcript).digest()
        if version == 0x12:
            prefix_unit = b'dmtf-spdm-v1.2.*'
        elif version == 0x13:
            prefix_unit = b'dmtf-spdm-v1.3.*'
        else:
            return transcript_hash, b''
        prefix = prefix_unit * 4
        if context == b'responder-challenge_auth signing':
            prefix_context = prefix + (b'\x00' * 4) + context
        elif context == b'responder-measurements signing':
            prefix_context = prefix + (b'\x00' * 6) + context
        else:
            raise ValueError('Unsupported SPDM signing context')
        if len(prefix_context) != 100:
            raise AssertionError('SPDM prefix/context must be 100 bytes')
        signing_input = prefix_context + transcript_hash
        return hashlib.sha384(signing_input).digest(), prefix_unit

    def _sign_response(
        self,
        version: int,
        transcript: bytes,
        unsigned_response: bytes,
        context: bytes,
    ) -> bytes:
        digest, prefix_unit = self._spdm_signing_digest(version, transcript, context)
        # The Certificate Chain leaf is the Measurement Certificate, so SPDM
        # CHALLENGE_AUTH and MEASUREMENTS must use the Measurement private key.
        signature_der = self.artifacts.measurement_key.sign(
            digest,
            ec.ECDSA(utils.Prehashed(hashes.SHA384())),
        )
        signature_raw = ecdsa_der_to_raw_p384(signature_der)
        self.last_signature_input = digest
        self.last_signature_raw = signature_raw
        LOG.info(
            'SPDM signing: version=%d.%d context=%s transcript_bytes=%d transcript_sha384=%s digest=%s',
            version >> 4,
            version & 0x0F,
            context.decode('ascii'),
            len(transcript),
            hashlib.sha384(transcript).hexdigest().upper(),
            digest.hex().upper(),
        )
        return unsigned_response + signature_raw

    def _track_exchange(self, request: bytes, response: bytes) -> None:
        code = request[1] if len(request) >= 2 else 0
        if code in (SPDM_GET_VERSION, SPDM_GET_CAPABILITIES, SPDM_NEGOTIATE_ALGORITHMS):
            self.transcript_a.extend(request)
            self.transcript_a.extend(response)
        elif code == SPDM_GET_DIGESTS:
            # GET_DIGESTS starts Message B.
            self.transcript_b.clear()
            self.transcript_b.extend(request)
            self.transcript_b.extend(response)
        # GET_CERTIFICATE is committed in get_certificate() as a logical pair.

    def _allocate_rnr_token(self) -> int:
        token = self.next_rnr_token & 0xFF
        if token == 0:
            token = 1
        self.next_rnr_token = (token + 1) & 0xFF
        return token

    @staticmethod
    def _response_not_ready(
        version: int,
        request_code: int,
        token: int,
    ) -> bytes:
        # ERROR(ResponseNotReady) extended data:
        # RDTExponent, RequestCode, Token, RDTM.
        return (
            spdm_header(
                version,
                SPDM_ERROR,
                SPDM_ERROR_CODE_RESPONSE_NOT_READY,
                0,
            )
            + bytes((
                SPDM_RNR_RDT_EXPONENT,
                request_code & 0xFF,
                token & 0xFF,
                SPDM_RNR_RDTM,
            ))
        )

    def _defer_signed_response(self, request: bytes) -> bytes:
        version = request[0]
        request_code = request[1]

        # Build and sign now, but do not expose the result until RESPOND_IF_READY.
        # challenge()/get_measurements() commit their normal transcript changes
        # exactly once; RNR and RESPOND_IF_READY wrappers are excluded.
        if request_code == SPDM_CHALLENGE:
            final_response = self.challenge(request)
        elif request_code == SPDM_GET_MEASUREMENTS:
            final_response = self.get_measurements(request)
        else:
            return self.error_response(version, 0x07, request_code)

        token = self._allocate_rnr_token()
        self.deferred_response = DeferredSpdmResponse(
            request_code=request_code,
            token=token,
            original_request=bytes(request),
            final_response=final_response,
        )
        LOG.info(
            'TX RESPONSE_NOT_READY request=0x%02X token=0x%02X EXP=%d RDTM=%d',
            request_code,
            token,
            SPDM_RNR_RDT_EXPONENT,
            SPDM_RNR_RDTM,
        )
        return self._response_not_ready(version, request_code, token)

    def respond_if_ready(self, request: bytes) -> bytes:
        if len(request) < 4:
            return self.error_response(SPDM_VERSION_12, 0x01, 0)
        # Requester wire format: Header.Param1=RequestCode,
        # Header.Param2=Token.
        version, _, request_code, token = request[:4]
        pending = self.deferred_response
        if (
            pending is None
            or token != pending.token
            or request_code != pending.request_code
        ):
            LOG.warning(
                'Invalid RESPOND_IF_READY request/token: got=%02X/%02X pending=%s',
                request_code,
                token,
                'none' if pending is None else
                f'{pending.request_code:02X}/{pending.token:02X}',
            )
            return self.error_response(version, 0x01, 0)

        response = pending.final_response
        self.deferred_response = None
        LOG.info(
            'RESPOND_IF_READY accepted request=0x%02X token=0x%02X; '
            'returning deferred response code=0x%02X bytes=%d',
            request_code,
            token,
            response[1],
            len(response),
        )
        return response

    def handle(self, request: bytes) -> bytes:
        if len(request) >= 2 and request[1] == SPDM_GET_VERSION:
            self.reset_protocol_state('GET_VERSION received')

        if len(request) >= 2 and request[1] == SPDM_RESPOND_IF_READY:
            # RESPOND_IF_READY is a transport/control wrapper and is not added
            # to Message A, Message B, or Message M.
            return self.respond_if_ready(request)

        if len(request) >= 2 and request[1] == SPDM_CHALLENGE:
            if self.deferred_response is not None:
                LOG.warning('New deferred request received while one is pending')
                return self.error_response(request[0], 0x03, 0)
            return self._defer_signed_response(request)

        if len(request) >= 2 and request[1] == SPDM_GET_MEASUREMENTS:
            if self.deferred_response is not None:
                LOG.warning('New deferred request received while one is pending')
                return self.error_response(request[0], 0x03, 0)

            # Match the firmware behavior: only a signed GET_MEASUREMENTS
            # request requires asynchronous signing and therefore RNR/RIR.
            # Unsigned requests are completed immediately.
            signature_requested = bool(
                request[2] &
                SPDM_GET_MEASUREMENTS_REQUEST_ATTRIBUTES_GENERATE_SIGNATURE
            )
            if signature_requested:
                return self._defer_signed_response(request)
            return self.get_measurements(request)

        response = super().handle(request)
        self._track_exchange(request, response)
        return response

    def challenge(self, request: bytes) -> bytes:
        if len(request) < 4 + SPDM_NONCE_SIZE:
            return self.error_response(SPDM_VERSION_12, 0x01, 0)
        version, _, slot_id, measurement_summary_type = request[:4]
        if (slot_id & 0x0F) != SPDM_SLOT_ID:
            return self.error_response(version, 0x01, 0)
        requester_nonce = request[4:36]
        responder_nonce = hashlib.sha256(
            b'challenge-response' + requester_nonce + bytes((measurement_summary_type,))
        ).digest()
        cert_chain_hash = hashlib.sha384(self.artifacts.chain).digest()
        measurement_summary_hash = b''
        if measurement_summary_type != 0:
            measurement_summary_hash = hashlib.sha384(b''.join(self.measurement_records)).digest()
        unsigned = (
            spdm_header(version, SPDM_CHALLENGE_AUTH, SPDM_SLOT_ID, SPDM_SLOT_MASK)
            + cert_chain_hash
            + responder_nonce
            + measurement_summary_hash
            + struct.pack('<H', 0)
        )
        transcript = bytes(self.transcript_a) + bytes(self.transcript_b) + request + unsigned
        response = self._sign_response(
            version,
            transcript,
            unsigned,
            b'responder-challenge_auth signing',
        )
        # Match requester/libspdm reset after CHALLENGE completion.
        self.transcript_b.clear()
        LOG.info('TX CHALLENGE_AUTH unsigned=%d signature=96', len(unsigned))
        return response

    def get_measurements(self, request: bytes) -> bytes:
        if len(request) < 4:
            return self.error_response(SPDM_VERSION_12, 0x01, 0)

        version, _, attributes, operation = request[:4]
        signature_requested = bool(
            attributes &
            SPDM_GET_MEASUREMENTS_REQUEST_ATTRIBUTES_GENERATE_SIGNATURE
        )

        if signature_requested:
            # SPDM 1.0 signed GET_MEASUREMENTS is header + nonce (36 bytes).
            # SlotIDParam was added in SPDM 1.1, so versions 1.1 and later use
            # header + nonce + SlotIDParam (37 bytes).
            minimum_request_size = 36 if version == SPDM_VERSION_10 else 37
            if len(request) < minimum_request_size:
                return self.error_response(version, 0x01, 0)

            requester_nonce = request[4:36]
            if version == SPDM_VERSION_10:
                slot_id_param = SPDM_SLOT_ID
            else:
                slot_id_param = request[36] & 0x0F
                if slot_id_param not in (SPDM_SLOT_ID, 0x0F):
                    return self.error_response(version, 0x01, 0)
        else:
            requester_nonce = b''
            slot_id_param = SPDM_SLOT_ID

        if operation == SPDM_MEASUREMENT_OPERATION_TOTAL_NUMBER:
            record = b''
            response_header_param1 = len(self.measurement_records)
            number_of_blocks = 0
        elif operation == SPDM_MEASUREMENT_OPERATION_ALL:
            record = b''.join(self.measurement_records)
            response_header_param1 = 0
            number_of_blocks = len(self.measurement_records)
        elif 1 <= operation <= len(self.measurement_records):
            record = self.measurement_records[operation - 1]
            response_header_param1 = 0
            number_of_blocks = 1
        else:
            return self.error_response(version, 0x01, operation)

        response_param2 = 0
        if signature_requested:
            response_param2 = (
                slot_id_param |
                SPDM_MEASUREMENTS_PARAM2_CONTENT_CHANGED_NOT_SUPPORTED
            )

        responder_nonce = hashlib.sha256(
            b'measurements-response' +
            bytes((operation, attributes)) +
            requester_nonce +
            record
        ).digest()
        opaque_data = b''

        # Match the project libspdm implementation exactly. The Nonce and
        # OpaqueDataLength are included even for unsigned Operation 0x00.
        unsigned = (
            spdm_header(
                version,
                SPDM_MEASUREMENTS,
                response_header_param1,
                response_param2,
            )
            + bytes((number_of_blocks,))
            + len(record).to_bytes(3, 'little')
            + record
            + responder_nonce
            + len(opaque_data).to_bytes(2, 'little')
            + opaque_data
        )

        if signature_requested:
            transcript = (
                bytes(self.transcript_a)
                + bytes(self.measurement_transcript)
                + request
                + unsigned
            ) if version >= 0x12 else (
                bytes(self.measurement_transcript) + request + unsigned
            )
            response = self._sign_response(
                version,
                transcript,
                unsigned,
                b'responder-measurements signing',
            )
            self.measurement_transcript.clear()
        else:
            response = unsigned
            self.measurement_transcript.extend(request)
            self.measurement_transcript.extend(response)

        if len(response) > FC65_MAX_BYTE_COUNT:
            handle = self.next_handle & 0xFF or 1
            self.next_handle = handle + 1
            self.chunk_transfer = ChunkTransfer(
                handle=handle,
                logical_response=response,
            )
            return self.error_response(
                version,
                SPDM_ERROR_CODE_LARGE_RESPONSE,
                handle,
            )

        LOG.info(
            'TX MEASUREMENTS operation=0x%02X blocks=%d record=%d '
            'nonce=32 opaque=0 signed=%s response=%d',
            operation,
            number_of_blocks,
            len(record),
            signature_requested,
            len(response),
        )
        return response

class DryRunValidator:
    def __init__(self, responder: EnhancedSpdmFc65Responder):
        self.responder = responder
        self.lines: list[str] = []

    def log(self, text: str) -> None:
        self.lines.append(text)

    def verify_last_signature(self, response: bytes) -> None:
        signature_raw = response[-SPDM_P384_SIGNATURE_SIZE:]
        if signature_raw != self.responder.last_signature_raw:
            raise AssertionError('Response signature does not match responder capture')
        self.responder.artifacts.measurement_key.public_key().verify(
            ecdsa_raw_to_der_p384(signature_raw),
            self.responder.last_signature_input,
            ec.ECDSA(utils.Prehashed(hashes.SHA384())),
        )

    def exchange(self, request: bytes, expected_code: int) -> bytes:
        frame = self.responder.build_fc65_frame(request)
        if crc16_modbus(frame[:-2]) != struct.unpack_from('<H', frame, len(frame) - 2)[0]:
            raise AssertionError('Generated request FC65 CRC mismatch')
        response = self.responder.handle(request)
        response_frame = self.responder.build_fc65_frame(response)
        if crc16_modbus(response_frame[:-2]) != struct.unpack_from('<H', response_frame, len(response_frame) - 2)[0]:
            raise AssertionError('Generated response FC65 CRC mismatch')
        if response[1] != expected_code:
            raise AssertionError(
                f'Expected response 0x{expected_code:02X}, got 0x{response[1]:02X}'
            )
        self.log(f'PASS request=0x{request[1]:02X} response=0x{response[1]:02X} bytes={len(response)}')
        return response

    def run(self) -> str:
        self.responder.reset_protocol_state('DRY RUN start')
        self.exchange(spdm_header(SPDM_VERSION_10, SPDM_GET_VERSION), SPDM_VERSION)
        self.exchange(spdm_header(SPDM_VERSION_12, SPDM_GET_CAPABILITIES) + bytes(16), SPDM_CAPABILITIES)
        self.exchange(spdm_header(SPDM_VERSION_12, SPDM_NEGOTIATE_ALGORITHMS) + bytes(28), SPDM_ALGORITHMS)
        self.exchange(spdm_header(SPDM_VERSION_12, SPDM_GET_DIGESTS), SPDM_DIGESTS)

        certificate_request = (
            spdm_header(SPDM_VERSION_12, SPDM_GET_CERTIFICATE, SPDM_SLOT_ID, 0)
            + struct.pack('<HH', 0, 0xFFFF)
        )
        large = self.exchange(certificate_request, SPDM_ERROR)
        if large[2] != SPDM_ERROR_CODE_LARGE_RESPONSE:
            raise AssertionError('Expected LARGE_RESPONSE')
        handle = large[4]
        sequence = 0
        reassembled = bytearray()
        while self.responder.chunk_transfer is not None:
            chunk_request = spdm_header(SPDM_VERSION_12, SPDM_CHUNK_GET, 0, handle) + struct.pack('<H', sequence)
            chunk_response = self.exchange(chunk_request, SPDM_CHUNK_RESPONSE)
            header_size = 16 if sequence == 0 else 12
            chunk_size = struct.unpack_from('<I', chunk_response, 8)[0]
            reassembled.extend(chunk_response[header_size:header_size + chunk_size])
            sequence += 1
        if bytes(reassembled)[:2] != bytes((SPDM_VERSION_12, SPDM_CERTIFICATE)):
            raise AssertionError('Reassembled logical certificate response is invalid')
        self.log(f'PASS certificate CHUNK reassembly logical_bytes={len(reassembled)} chunks={sequence}')

        challenge_request = (
            spdm_header(SPDM_VERSION_12, SPDM_CHALLENGE, SPDM_SLOT_ID, 0)
            + SPDM_DRYRUN_REQUEST_NONCE
        )
        challenge_rnr = self.exchange(challenge_request, SPDM_ERROR)
        if challenge_rnr[2] != SPDM_ERROR_CODE_RESPONSE_NOT_READY:
            raise AssertionError('CHALLENGE did not return RESPONSE_NOT_READY')
        if challenge_rnr[5] != SPDM_CHALLENGE:
            raise AssertionError('CHALLENGE RNR RequestCode mismatch')
        if challenge_rnr[4] != SPDM_RNR_RDT_EXPONENT or challenge_rnr[7] != SPDM_RNR_RDTM:
            raise AssertionError('CHALLENGE RNR EXP/RDTM mismatch')
        challenge_rir = spdm_header(
            SPDM_VERSION_12,
            SPDM_RESPOND_IF_READY,
            SPDM_CHALLENGE,
            challenge_rnr[6],
        )
        challenge_response = self.exchange(challenge_rir, SPDM_CHALLENGE_AUTH)
        self.verify_last_signature(challenge_response)
        self.log('PASS CHALLENGE RNR/RIR EXP=19 RDTM=2 and signature verification')

        count_request = spdm_header(
            SPDM_VERSION_12,
            SPDM_GET_MEASUREMENTS,
            0x00,
            SPDM_MEASUREMENT_OPERATION_TOTAL_NUMBER,
        )
        # Unsigned GET_MEASUREMENTS must complete immediately without
        # RESPONSE_NOT_READY or RESPOND_IF_READY.
        count_response = self.exchange(count_request, SPDM_MEASUREMENTS)
        if len(count_response) != 42:
            raise AssertionError(
                'GET_MEASUREMENTS total-number response must be 42 bytes '
                f'(fixed=8, nonce=32, opaque_length=2), got {len(count_response)}'
            )
        if count_response[2] != len(self.responder.measurement_records):
            raise AssertionError('GET_MEASUREMENTS total-number count mismatch')
        if count_response[3] != 0:
            raise AssertionError('Total-number Header.Param2 must be zero')
        if count_response[4] != 0:
            raise AssertionError('Total-number NumberOfBlocks must be zero')
        if int.from_bytes(count_response[5:8], 'little') != 0:
            raise AssertionError('Total-number MeasurementRecordLength must be zero')
        if len(count_response[8:40]) != SPDM_NONCE_SIZE:
            raise AssertionError('Total-number responder nonce must be 32 bytes')
        if int.from_bytes(count_response[40:42], 'little') != 0:
            raise AssertionError('Total-number OpaqueDataLength must be zero')
        self.log(
            'PASS GET_MEASUREMENTS total-number response size=42: '
            'count=2, NumberOfBlocks=0, record=0, nonce=32, opaque=0; '
            'direct response without RNR/RIR'
        )

        measurement_request = (
            spdm_header(
                SPDM_VERSION_12,
                SPDM_GET_MEASUREMENTS,
                0x01,
                SPDM_MEASUREMENT_OPERATION_ALL,
            )
            + bytes(reversed(SPDM_DRYRUN_REQUEST_NONCE))
            + bytes((SPDM_SLOT_ID,))
        )
        measurement_rnr = self.exchange(measurement_request, SPDM_ERROR)
        if measurement_rnr[2] != SPDM_ERROR_CODE_RESPONSE_NOT_READY:
            raise AssertionError('Signed GET_MEASUREMENTS did not return RESPONSE_NOT_READY')
        if measurement_rnr[4] != SPDM_RNR_RDT_EXPONENT or measurement_rnr[5] != SPDM_GET_MEASUREMENTS:
            raise AssertionError('Signed GET_MEASUREMENTS RNR EXP/RequestCode mismatch')
        if measurement_rnr[7] != SPDM_RNR_RDTM:
            raise AssertionError('Signed GET_MEASUREMENTS RNR RDTM mismatch')
        measurement_rir = spdm_header(
            SPDM_VERSION_12,
            SPDM_RESPOND_IF_READY,
            SPDM_GET_MEASUREMENTS,
            measurement_rnr[6],
        )
        measurement_response = self.exchange(measurement_rir, SPDM_MEASUREMENTS)
        self.verify_last_signature(measurement_response)
        self.log('PASS MEASUREMENTS RNR/RIR and P-384/SHA-384 signature verification')

        # Regression test for SPDM 1.0: signed GET_MEASUREMENTS omits
        # SlotIDParam and therefore has exactly 36 bytes on the wire.
        measurement_v10_request = (
            spdm_header(
                SPDM_VERSION_10,
                SPDM_GET_MEASUREMENTS,
                SPDM_GET_MEASUREMENTS_REQUEST_ATTRIBUTES_GENERATE_SIGNATURE,
                0x02,
            )
            + SPDM_DRYRUN_REQUEST_NONCE
        )
        if len(measurement_v10_request) != 36:
            raise AssertionError('SPDM 1.0 signed GET_MEASUREMENTS must be 36 bytes')
        measurement_v10_rnr = self.exchange(measurement_v10_request, SPDM_ERROR)
        if measurement_v10_rnr[2] != SPDM_ERROR_CODE_RESPONSE_NOT_READY:
            raise AssertionError('SPDM 1.0 signed GET_MEASUREMENTS did not return RNR')
        measurement_v10_rir = spdm_header(
            SPDM_VERSION_10,
            SPDM_RESPOND_IF_READY,
            SPDM_GET_MEASUREMENTS,
            measurement_v10_rnr[6],
        )
        measurement_v10_response = self.exchange(
            measurement_v10_rir,
            SPDM_MEASUREMENTS,
        )
        if measurement_v10_response[0] != SPDM_VERSION_10:
            raise AssertionError('SPDM 1.0 deferred MEASUREMENTS version mismatch')
        self.verify_last_signature(measurement_v10_response)
        self.log(
            'PASS SPDM 1.0 signed MEASUREMENTS: 36-byte request, no '
            'SlotIDParam, RNR/RIR, P-384/SHA-384 signature verification'
        )
        self.log('DRY RUN COMPLETE: all structural, CRC, CHUNK, certificate, challenge, and measurement checks passed')
        return '\n'.join(self.lines)


class TkLogHandler(logging.Handler):
    def __init__(self, target_queue: queue.Queue[str]):
        super().__init__()
        self.target_queue = target_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.target_queue.put(self.format(record))


class ResponderGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SPDM FC65 Responder Emulator GUI v14')
        self.geometry('1050x720')
        self.minsize(900, 620)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.listener_thread: Optional[threading.Thread] = None
        self.artifacts: Optional[CertificateArtifacts] = None
        self.responder: Optional[EnhancedSpdmFc65Responder] = None
        self._build_widgets()
        self._install_logging()
        self.after(0, self._load_existing_on_startup)
        self.after(100, self._drain_log_queue)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        connection = ttk.LabelFrame(outer, text='Responder configuration', padding=10)
        connection.pack(fill=tk.X)

        self.port_var = tk.StringVar()
        self.baud_rate_var = tk.StringVar(value=str(SERIAL_DEFAULT_BAUD_RATE))
        self.parity_var = tk.StringVar(value='Even')
        self.address_var = tk.StringVar(value='0x80')
        self.device_id_var = tk.StringVar(value='STM-1e0041000e50314351383320')
        self.output_var = tk.StringVar(value=str(Path.cwd() / 'spdm_emulator_artifacts'))
        self.status_var = tk.StringVar(value='Stopped')

        ttk.Label(connection, text='COM Port').grid(
            row=0, column=0, sticky='w', padx=(0, 8), pady=4)
        self.port_combobox = ttk.Combobox(
            connection,
            textvariable=self.port_var,
            state='readonly',
            width=30,
        )
        self.port_combobox.grid(row=0, column=1, sticky='ew', pady=4)
        ttk.Button(
            connection,
            text='Refresh COM Ports',
            command=self._refresh_com_ports,
        ).grid(row=0, column=2, padx=(8, 0), pady=4)

        serial_options = ttk.Frame(connection)
        serial_options.grid(row=1, column=1, sticky='w', pady=4)

        ttk.Label(connection, text='Serial Settings').grid(
            row=1, column=0, sticky='w', padx=(0, 8), pady=4)
        ttk.Label(serial_options, text='Bitrate').pack(side=tk.LEFT)
        self.baud_rate_combobox = ttk.Combobox(
            serial_options,
            textvariable=self.baud_rate_var,
            values=[str(value) for value in SERIAL_BAUD_RATE_OPTIONS],
            state='normal',
            width=12,
        )
        self.baud_rate_combobox.pack(side=tk.LEFT, padx=(6, 18))

        ttk.Label(serial_options, text='Parity').pack(side=tk.LEFT)
        self.parity_combobox = ttk.Combobox(
            serial_options,
            textvariable=self.parity_var,
            values=list(SERIAL_PARITY_OPTIONS.keys()),
            state='readonly',
            width=10,
        )
        self.parity_combobox.pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(connection, text='Slave Address').grid(
            row=2, column=0, sticky='w', padx=(0, 8), pady=4)
        ttk.Entry(connection, textvariable=self.address_var, width=72).grid(
            row=2, column=1, sticky='ew', pady=4)

        ttk.Label(connection, text='Device ID').grid(
            row=3, column=0, sticky='w', padx=(0, 8), pady=4)
        ttk.Entry(connection, textvariable=self.device_id_var, width=72).grid(
            row=3, column=1, sticky='ew', pady=4)

        ttk.Label(connection, text='Artifact Directory').grid(
            row=4, column=0, sticky='w', padx=(0, 8), pady=4)
        ttk.Entry(connection, textvariable=self.output_var, width=72).grid(
            row=4, column=1, sticky='ew', pady=4)
        ttk.Button(connection, text='Browse', command=self._browse_output).grid(
            row=4, column=2, padx=(8, 0), pady=4)

        connection.columnconfigure(1, weight=1)
        self._refresh_com_ports(log_result=False)

        buttons = ttk.Frame(outer, padding=(0, 10))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text='Regenerate Keys and Certificates', command=self._generate).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text='DRY RUN Validation', command=self._dry_run).pack(side=tk.LEFT, padx=(0, 8))
        self.start_button = ttk.Button(buttons, text='Start COM Responder', command=self._start)
        self.start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text='Stop', command=self._stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text='Clear Log', command=lambda: self.log_text.delete('1.0', tk.END)).pack(side=tk.LEFT)
        ttk.Label(buttons, textvariable=self.status_var).pack(side=tk.RIGHT)

        info = ttk.LabelFrame(outer, text='Implemented SPDM flow', padding=8)
        info.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            info,
            text=('VERSION, CAPABILITIES, ALGORITHMS, DIGESTS, CERTIFICATE, CHUNK, '
                  'CHALLENGE_AUTH and signed MEASUREMENTS with RNR/RIR EXP=19 RDTM=2; unsigned MEASUREMENTS respond immediately. '
                  '1 stop bit, selectable bitrate/parity, and FC65.'),
        ).pack(anchor='w')

        log_frame = ttk.LabelFrame(outer, text='Responder log', padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_frame, wrap=tk.NONE, font=('Consolas', 10))
        yscroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        xscroll = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.log_text.grid(row=0, column=0, sticky='nsew')
        yscroll.grid(row=0, column=1, sticky='ns')
        xscroll.grid(row=1, column=0, sticky='ew')
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

    def _install_logging(self) -> None:
        LOG.setLevel(logging.DEBUG)
        handler = TkLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter('[%(asctime)s.%(msecs)03d] %(message)s', '%H:%M:%S'))
        LOG.handlers.clear()
        LOG.addHandler(handler)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.insert(tk.END, line + '\n')
                self.log_text.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get())
        if selected:
            self.output_var.set(selected)

    def _refresh_com_ports(self, log_result: bool = True) -> None:
        previous_selection = self.port_var.get().strip()
        detected_ports = sorted(
            list_ports.comports(),
            key=lambda item: item.device.lower(),
        )
        port_names = [item.device for item in detected_ports]
        self.port_combobox.configure(values=port_names)

        if previous_selection in port_names:
            self.port_var.set(previous_selection)
        elif port_names:
            self.port_var.set(port_names[0])
        else:
            self.port_var.set('')

        if log_result:
            if detected_ports:
                LOG.info(
                    'COM scan found %d port(s): %s',
                    len(detected_ports),
                    ', '.join(
                        f'{item.device} ({item.description})'
                        for item in detected_ports
                    ),
                )
            else:
                LOG.warning('COM scan found no available serial ports')

    def _serial_settings(self) -> tuple[int, str, str]:
        port_name = self.port_var.get().strip()
        if not port_name:
            raise ValueError(
                'No COM Port is selected. Connect the adapter and click Refresh COM Ports.'
            )

        try:
            baud_rate = int(self.baud_rate_var.get().strip(), 10)
        except ValueError as exc:
            raise ValueError('Bitrate must be a positive integer') from exc

        if baud_rate <= 0:
            raise ValueError('Bitrate must be a positive integer')

        parity_name = self.parity_var.get().strip()
        if parity_name not in SERIAL_PARITY_OPTIONS:
            raise ValueError(f'Unsupported parity setting: {parity_name}')

        return port_name, baud_rate, SERIAL_PARITY_OPTIONS[parity_name]

    def _address(self) -> int:
        value = int(self.address_var.get().strip(), 0)
        if not 1 <= value <= 247:
            raise ValueError('Slave Address must be from 1 through 247')
        return value

    def _artifact_directory(self) -> Path:
        return Path(self.output_var.get()).expanduser()

    def _activate_artifacts(
        self,
        artifacts: CertificateArtifacts,
        source: str,
    ) -> None:
        self.artifacts = artifacts
        self.responder = EnhancedSpdmFc65Responder(
            self._address(),
            artifacts,
        )
        LOG.info(
            '%s four-certificate P-384 identity: chain=%d bytes, directory=%s',
            source,
            len(artifacts.chain),
            self._artifact_directory().resolve(),
        )

    def _load_existing_on_startup(self) -> None:
        output_dir = self._artifact_directory()
        if not output_dir.exists():
            LOG.info(
                'Artifact directory does not exist yet. Keys will be generated '
                'when DRY RUN or COM Responder is started.'
            )
            return
        if not artifact_files_exist(output_dir):
            LOG.warning(
                'Artifact directory exists but the reusable key/certificate set '
                'is incomplete. No files were overwritten. Press Regenerate Keys '
                'and Certificates to create a complete new identity.'
            )
            return
        try:
            self._activate_artifacts(
                load_artifacts(output_dir),
                'Loaded existing',
            )
        except Exception as exc:
            LOG.error('Existing artifact validation failed: %s', exc)
            messagebox.showerror(
                'Existing artifacts are invalid',
                f'{exc}\n\nNo files were overwritten. Use Regenerate Keys and '
                'Certificates only if a new identity is intended.',
            )

    def _generate(self) -> None:
        """Explicitly regenerate and overwrite the emulator identity."""
        try:
            output_dir = self._artifact_directory()
            self.artifacts = generate_certificate_chain(
                self.device_id_var.get().strip()
            )
            write_artifacts(output_dir, self.artifacts)
            self._activate_artifacts(self.artifacts, 'Regenerated')
        except Exception as exc:
            LOG.error('Regeneration failed: %s', exc)
            messagebox.showerror('Regeneration failed', str(exc))

    def _load_or_generate(self) -> None:
        """Reuse a valid identity when present, otherwise generate it once."""
        if self.responder is not None and self.artifacts is not None:
            return
        output_dir = self._artifact_directory()
        if artifact_files_exist(output_dir):
            self._activate_artifacts(
                load_artifacts(output_dir),
                'Loaded existing',
            )
            return
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError(
                'Artifact directory exists but is incomplete. No files were '
                'overwritten. Press Regenerate Keys and Certificates to replace '
                'the directory with a complete new identity.'
            )
        artifacts = generate_certificate_chain(self.device_id_var.get().strip())
        write_artifacts(output_dir, artifacts)
        self._activate_artifacts(artifacts, 'Generated initial')

    def _dry_run(self) -> None:
        def worker() -> None:
            try:
                self._load_or_generate()
                if self.responder is None or self.artifacts is None:
                    return
                LOG.info('DRY RUN started. COM Port will not be opened.')
                test_responder = EnhancedSpdmFc65Responder(
                    self._address(), self.artifacts
                )
                report = DryRunValidator(test_responder).run()
                for line in report.splitlines():
                    LOG.info(line)
                report_path = self._artifact_directory() / 'dry_run_report.txt'
                report_path.write_text(report + '\n', encoding='utf-8')
                LOG.info('DRY RUN report: %s', report_path.resolve())
            except Exception:
                LOG.error('DRY RUN failed:\n%s', traceback.format_exc())
        threading.Thread(target=worker, daemon=True).start()

    def _start(self) -> None:
        if self.listener_thread and self.listener_thread.is_alive():
            return
        try:
            self._load_or_generate()
            if self.responder is None:
                return
            port_name, baud_rate, parity = self._serial_settings()
            address = self._address()
        except Exception as exc:
            messagebox.showerror('Invalid configuration', str(exc))
            return
        self.stop_event.clear()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set('Listening')
        self.listener_thread = threading.Thread(
            target=self._serial_worker,
            args=(port_name, baud_rate, parity, address),
            daemon=True,
        )
        self.listener_thread.start()

    def _serial_worker(
        self,
        port_name: str,
        baud_rate: int,
        parity: str,
        address: int,
    ) -> None:
        try:
            with serial.Serial(
                port=port_name,
                baudrate=baud_rate,
                bytesize=SERIAL_DATA_BITS,
                parity=parity,
                stopbits=SERIAL_STOP_BITS,
                timeout=0.05,
                write_timeout=1.0,
            ) as port:
                reader = SerialFrameReader(port, address)
                parity_short = SERIAL_PARITY_SHORT_NAMES.get(parity, str(parity))
                LOG.info(
                    'OPEN %s %d 8%s1 address=0x%02X FC65',
                    port_name,
                    baud_rate,
                    parity_short,
                    address,
                )
                while not self.stop_event.is_set():
                    frame = reader.read_frame()
                    if frame is None:
                        time.sleep(0.001)
                        continue
                    LOG.info('RX FC65 (%d bytes)\n%s', len(frame), hex_bytes(frame))
                    response = self.responder.handle(frame[3:-2])
                    response_frame = self.responder.build_fc65_frame(response)
                    port.write(response_frame)
                    port.flush()
                    LOG.info('TX FC65 (%d bytes)\n%s', len(response_frame), hex_bytes(response_frame))
        except Exception:
            LOG.error('COM responder stopped by error:\n%s', traceback.format_exc())
        finally:
            self.after(0, self._listener_stopped)

    def _listener_stopped(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.status_var.set('Stopped')

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_var.set('Stopping')

    def _on_close(self) -> None:
        self.stop_event.set()
        self.destroy()


def gui_main() -> int:
    app = ResponderGui()
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(gui_main())
