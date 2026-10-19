admin@artesyn-shelf:~# fru-tool.sh
{"fru": {"config": {"0": 0, "1": 0}, "aei": {"0": "Powershelf", "1": "Advanced Energy", "2": "2024-07-05", "3": "Philippines", "4": "700-055600-0000", "5": "CUST-500-1001", "6": "X01", "7": "v01.00.00", "8": "1022S3V3AEL00026", "9": "pmc1"}, "spec": {"0": "33kW"}}, "ver": 0}
admin@artesyn-shelf:~# which fru-tool.sh | xargs cat
#!/usr/bin/python3

# Python included Packages
import argparse, json, os, datetime, asyncio,time, subprocess

# External Packages
import cbor2
import gpiod

# flags
device='none'
is_shelf=False
is_write=False
is_write_protect=False
is_format_json=False
is_checksum=False

# initialize config variables
CFG_EEPROM_FD=''
CFG_EEPROM_RANGE_START=''
CFG_EEPROM_RANGE_END=''
CFG_EEPROM_RANGE_LENGTH=''
CFG_EEPROM_ADDRESS_MODE=''

CFG_EEPROM_WP_GPIO_CHIP=''
CFG_EEPROM_WP_GPIO_PIN=''
CFG_EEPROM_WRITE_PROTECT=''
CFG_EEPROM_WP_ACTIVE_STATE=''
CFG_DATA_SCHEMA=''

FRU_OFFSET=0
FRU_LENGTH=0

parser=argparse.ArgumentParser(
    description='''fru-tool python ver. ''',
    epilog="""All is well that ends well.""")
parser.add_argument('--device',metavar='',action="store",dest="devicetype", type=str, default="shelf", help='[ manager/shelf ] EEPROM Device to interface with')
parser.add_argument('--operation',metavar='',action="store",dest="operation",nargs="+", type=str, default="read", help='[ read/write (data)] Read or Write Operation')
parser.add_argument('--format',metavar='',action="store",dest="format", type=str, default="json", help='[ raw/json ] Data Format.')
parser.add_argument('--checksum',action="store_true",dest="checksum",help='Perform checksum computation/verification.')
parser.add_argument('--offset',metavar='',action="store",dest="offset", type=int, default=0, help='[n] data offset (optional). -- Not yet Implemented')
parser.add_argument('--length',metavar='',action="store",dest="length", type=int, default=0, help='[n]  data length in bytes (optional). -- Not yet Implemented')
parser.add_argument('--load-sample-data',action="store_true",dest="default", help=' Load dummy Data')
parser.add_argument('--erase-eeprom-data',action="store_true",dest="erase", help='Erase EEPROM')
parser.add_argument('--behave',action="store_true",dest="behave",help='Enable when performing behavioral testing')

# Add verbose argument for responses
args=parser.parse_args()

def logs(msg):
    time_now = datetime.datetime.now()
    time_now = time_now.strftime('%Y-%m-%d %H:%M:%S')
    LOGS_MSG="{" + "logtable: {}, timestamp: {}, name: FRU Tool, description: Error Logging, eventtype: FAULT , message: {}".format(device,time_now,msg) + "}"
    interface = 'aei.osal.frutool.signals.logs'
    cmd = 'dbus-send --system --type=signal / {} string:"{}"'.format(interface, LOGS_MSG)
    res = subprocess.run(cmd,shell=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)

def write_dummy_default(FRU_OFFSET,FRU_LENGTH):
    dummy_data ='''{"sample_fru": {"sample_aei": {"sample_0": "Powershelf","sample_1": "Advanced Energy"}},"ver": 0}'''
    perform_fru_write(FRU_OFFSET,FRU_LENGTH,dummy_data)

def verify_eeprom(offset,FRU_LENGTH):
    # Check if EEPROM is detected
    if os.path.exists(CFG_EEPROM_FD) == False:
        print("Error: EEPROM does not exists")
        # logs("Error: EEPROM does not exists")
        exit()

    with open(CFG_EEPROM_FD, "rb") as fp:
        fp.seek(offset)
        data = fp.read(FRU_LENGTH)

    # Check if EEPROM was written with initial data
    total = sum(data)

    if total == 0 or total >= 261120:
        print("Error: EEPROM Section Empty. Perfrom an Initial Write to EEPROM")
        # logs("Error: EEPROM Section Empty. Perfrom an Initial Write to EEPROM")
        exit()

# Set EEPROM GPIO Write Protect Pins
def set_eeprom_write_protect(WP_SET_STATE):
    wp_pin = None

    #Will skip WP Pin set if there is no wp enabled or it is performing a behave test
    if CFG_EEPROM_WRITE_PROTECT == "no" or args.behave == True:
        return 0
    else:
        try:
            chip = gpiod.chip(CFG_EEPROM_WP_GPIO_CHIP)
            wp_pin = chip.get_line(CFG_EEPROM_WP_GPIO_PIN)

            config = gpiod.line_request()
            config.consumer = "eeprom-wp"
            config.request_type = gpiod.line_request.DIRECTION_OUTPUT

            wp_pin.request(config)
        except TypeError:
            print("EEPROM configuration does not exist")
            exit()

        if WP_SET_STATE == "enable":
            if CFG_EEPROM_WP_ACTIVE_STATE == "low":
                wp_pin.set_value(0)
            else:
                wp_pin.set_value(1)

        elif WP_SET_STATE == "disable":
            if CFG_EEPROM_WP_ACTIVE_STATE == "low":
                wp_pin.set_value(0)
            else:
                wp_pin.set_value(1)

    # pass
# Erase contents of EEPROM
def erase_eeprom(EEPROM_OFFSET,EEPROM_LENGTH):
    EEPROM_FILE=CFG_EEPROM_FD

    FRU_RAW_DATA = bytes.fromhex("".zfill(EEPROM_LENGTH*2))

    set_eeprom_write_protect("disable")
    with open(EEPROM_FILE, "w+b") as f:   # 'r+b' = read & write binary
        f.seek(EEPROM_OFFSET)
        f.write(FRU_RAW_DATA)

    set_eeprom_write_protect("enable")

def compute_checksum(data: bytes) -> int:
    total = sum(data) & 0xFF
    checksum = (-total) & 0xFF
    return checksum

# Used to get environment variables from the config file
def read_config_file():
    try:
        with open('/etc/fru-tool/fru-tool.cfg','r') as file:
            data = json.load(file)
        return data
    except FileNotFoundError:
        print("Error: Config File Not Found")
        exit()

# Write data to the EEPROM
def perform_fru_write(FRU_OFFSET,FRU_LENGTH,data):
    # If --format json args is passed
    if args.format == "json":

        #Convert inputted data into JSON Object
        data = json.loads(data)

        # Serialize JSON Object into CBOR data
        CBOR_DATA=cbor2.dumps(data)

        # get Schema Version
        SCHEMA_VERSION=data['ver']
        SCHEMA_VERSION = bytes.fromhex(format(int(SCHEMA_VERSION), '02x'))
        CBOR_DATA = SCHEMA_VERSION + CBOR_DATA

    # Otherwise data format is Raw
    else:
        CBOR_DATA = data.encode()

    # If checksum args is passed
    if args.checksum == True:
        COMPUTED_CHECKSUM = compute_checksum(CBOR_DATA) # Get computed checksum
        COMPUTED_CHECKSUM = bytes.fromhex(format(int(COMPUTED_CHECKSUM), '02x')) # Convert hex checksum into bytes
        CBOR_DATA = COMPUTED_CHECKSUM + CBOR_DATA

    # Otherwise checksum is \x00
    else:
        COMPUTED_CHECKSUM = bytes.fromhex('00')
        CBOR_DATA = COMPUTED_CHECKSUM + CBOR_DATA

    # Erases the eeprom with xFF and is then replaced with x00
    erase_eeprom(CFG_EEPROM_RANGE_START,CFG_EEPROM_RANGE_LENGTH)

    # Gets the EEPROM file directory
    EEPROM_FILE=CFG_EEPROM_FD

    if CBOR_DATA is not None:
        set_eeprom_write_protect("disable")
        with open(EEPROM_FILE, "r+b") as f:   # 'r+b' = read & write binary
            f.seek(FRU_OFFSET) # skip x number of bytes before write
            f.write(CBOR_DATA[:FRU_LENGTH])  # write only the desired length of bytes
        set_eeprom_write_protect("enable")

    else:
        print("Error: Data Bytes the dust!") #change message
        exit()

# Reads the data stored in the EEPROM
def perform_fru_read(offset,FRU_LENGTH):
    EEPROM_FILE=CFG_EEPROM_FD

    # Reads EEPROM FILE starting from offset
    # in fru-tool.cfg
    with open(EEPROM_FILE, "rb") as fp:
        fp.seek(offset)
        CBOR_DATA = fp.read(FRU_LENGTH)

    # If checksum argument is passed
    if args.checksum == True:
        RECEIVED_CHECKSUM = CBOR_DATA[0]
        COMPUTED_CHECKSUM = compute_checksum(CBOR_DATA[1:])
        if RECEIVED_CHECKSUM != COMPUTED_CHECKSUM:
            print("Error: Invalid checksum! rcvd: {} and comp: {}".format(RECEIVED_CHECKSUM,COMPUTED_CHECKSUM))
            exit()

    if args.format == "json":
        CBOR_DATA = cbor2.loads(CBOR_DATA[2:])# Deserialize cbor data to string
        CBOR_DATA = json.dumps(CBOR_DATA) # Encode into JSON
        print(CBOR_DATA)
    else:
        print(CBOR_DATA.decode())

# Main Operation
# Get Device Type
device = args.devicetype

# Set Config Variables
if device == "manager" or device == "shelf":
    # Read Config File
    data = read_config_file()

    CFG_EEPROM_FD=data[device]['file']
    CFG_EEPROM_RANGE_START=data[device]['range']['start']
    CFG_EEPROM_RANGE_END=data[device]['range']['end']
    CFG_EEPROM_RANGE_LENGTH= CFG_EEPROM_RANGE_END - CFG_EEPROM_RANGE_START + 1
    CFG_EEPROM_ADDRESS_MODE=data[device]['address']

    CFG_EEPROM_WRITE_PROTECT=data[device]['write_protect']
    CFG_EEPROM_WP_ACTIVE_STATE=data[device]['wp_active_state']
    CFG_EEPROM_WP_GPIO_CHIP=data[device]['wp_chip']
    CFG_EEPROM_WP_GPIO_PIN=data[device]['wp_pin']

    CFG_DATA_SCHEMA=data[device]['schema']

else:
    print("Error: Invalid Device found.")
    exit()

# Get FRU ENV Variables
FRU_OFFSET=CFG_EEPROM_RANGE_START
FRU_LENGTH=CFG_EEPROM_RANGE_LENGTH
FRU_END = CFG_EEPROM_RANGE_END

# New Features
if args.default ==True:
    args.format = 'json'
    write_dummy_default(FRU_OFFSET,FRU_LENGTH)
    exit()

# Erase EEPROM
if args.erase ==True:
    erase_eeprom(FRU_OFFSET,FRU_END) #Erase EEPROM from start to end
    exit()

if args.operation[0] == "write":
    data = args.operation[1]
    perform_fru_write(FRU_OFFSET,FRU_LENGTH,data)

else:
    verify_eeprom(FRU_OFFSET,FRU_LENGTH)
    perform_fru_read(FRU_OFFSET,FRU_LENGTH)