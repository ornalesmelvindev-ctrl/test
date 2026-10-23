as I said to you, currently, dlc must be empty, what I mean empty is it has no fan and power led, 
so I think no need for STD_DLC thing? Also as of now, we dont know how input/output voltage works

Also, dont use other unnecessary macros okay?

then dont hold back on naming variables or macros, readability is a must, a you can see for the existing macro
it enough to understand for what is that
like that BLOCK_COMMON, BLOCK_HPR, etc, so what right?

like consider, for now I understand the flow, what if other develop will add? Will they understand it easily? see those instances

also as we talked, this must be done in the main only right?
MODULE_VARIANT_DLC != get_device_profile()->variant

Like we can even pass function pointers in the streuct or table right? why not do that? your compressing all in one not readable table
well if function pointers are not needed, of course dont do

also you can simplify things like inside monitor_dynamic, its just calling monitor_dynamic_params using the same arguments
so maybe we can call direcitly the read dynamic and static readouts? like those things


also like in here
    /** input power */
    FIELD("pin_watts",                 d_inputPower,            BLOCK_COMMON,  MODBUS_REGISTER_OFFSET_INPUT_POWER,               AS_UINT16(3),      V_STD_DLC),
    FIELD("pin_watts",                 d_inputPower,            BLOCK_HPR,     MODBUS_REGISTER_OFFSET_INPUT_POWER,               AS_UINT16(3),      V_HPR),
    FIELD("pin_watts",                 d_inputPower,            BLOCK_HPR_V2,  MODBUS_REGISTER_OFFSET_HPR_V2_INPUT_POWER_AVERAGE, AS_UINT16(1),     V_HPR_V2),

    its like 3 fields with the same name
    cant we do just one row for i field, and just process else where the needed
    also dont hold back on creating tables, you're not obliged to compressed everything in one table

readability, scalability, maintainablity, consideartion for other developers is a must
because ofcourse, this is just for dynamic readouts, what more if I add the others
LIKE that AS_RAW or ENC_INT16, so what? what is that? right?
and this is the struct of deivce profile, just so you know
#ifndef __AEI_DEVICE_PROFILE_H__
#define __AEI_DEVICE_PROFILE_H__

#include <stdint.h>
#include <stdbool.h>

/** Device config file */
#define CONFIG_FILE ("/etc/ae-config/enabled/device-profile.cfg")

#define DEVICE_SHELF_ID_DEFAULT (1)
#define SUPPORTED_DEVICE_COUNT_DEFAULT (6)
#define DEVICE_START_MODBUS_ADDRESS_DEFAULT (0xC0)

typedef enum
{
    MODULE_TYPE_PSU = 0,
    MODULE_TYPE_BBU = 1
} module_type_et;

typedef enum
{
    MODULE_VARIANT_STANDARD = 0,
    MODULE_VARIANT_HPR      = 1,
    MODULE_VARIANT_HPR_V2   = 2,
    MODULE_VARIANT_DLC      = 3
} module_variant_et;

typedef struct
{
    uint8_t shelf_id;
    module_type_et type;
    module_variant_et variant;
    uint8_t supported_device_count;
    uint8_t start_address;
} device_profile_st;

int load_device_profile(void);
const device_profile_st *get_device_profile(void);

#endif // __AEI_DEVICE_PROFILE_H__


also, i want to do like``
this is our current
ls src          
CMakeLists.txt  ae-json.c  data-schema.c   dbus-pub.c      device-profile.c  include        modbus-master.c         monitor-config.c   monitor-list.c    poll-manager.c   variants
README.md       common.c   dbus-actions.c  dbus-service.c  helpers.c         logs-client.c  modbus-registers-map.c  monitor-dynamic.c  monitor-static.c  psu-detection.c

I added here the variants that has
dlc.c  hpr-v2.c  hpr.c  hpr.h  ops.c  std.c  var_common.c  var_common.h

ls src/include 
ae-json.h  config.h.in    dbus-actions.h  dbus-service.h    helpers.h      modbus-master.h         monitor-config.h   monitor-list.h    poll-manager.h   variant-ops.h
common.h   data-schema.h  dbus-pub.h      device-profile.h  logs-client.h  modbus-registers-map.h  monitor-dynamic.h  monitor-static.h  psu-detection.h

because if this table driven thing cant fit my and the taste of my superior, we might go back to our previous implementation


