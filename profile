For the PSU detection, I tried replicating the issue but after several trials, I failed to replicate it, PSU absence can be detected for about 10-15 sec and presence for about 30 sec(since present is being reported if all configs were ready), so I just closed it for retest

for the SPDM Emulators, I am able to run those from china team using virtual port and it seems to be working fine, with address of 0x80 and spdm_cmd of 0x65
also managed to create emulators in C, using the same modbus rtu, and behaves well just like those given emulators to us

for the osal optimizations phase 1, since the task is just to identify, I only identified one script, specifically the get_versions.sh to be refactored since it causes the delay of pmpmc starting for about 2 mins, and after the fix, this script now runs for only about 1-2sec
for the other scripts, just improvement but not that critical like the way we get if service is active

for the Optimize Dbus calls called via system calls, task also is to identify, so I checked all of our services with system busctl calls
cmpmc, cmpsu. pmdev, pmpmc, pmpsu and pmshelf were the services the do dbus systemcalls, I already prepared the equivalent of those calls using sd bus library

For the DLC unsupported features, I am done disabling those features in pmpsu, cmpsu and hkshelf but didnt test it yet

Fot  the osal cli/library, I am now on the testing stage, I already created image with the systemd patch, without the pmc network related supervisory services and the osal scripts, I didnt iclude yet the http and caddy related osal scripts
I noticed that in our current implementation, network restarts a lot, and since pmc-certificate-svc (which runs the caddy service) runs after network,target, I think it runs by luck for now
But now with my built image, network just starts once, but pmc-certificate-svc fails because it  inside it the pmc variant, which is in the device-profile.cfg, but device-profile.cfg is created my pmdev(device poll manager), so I think we can consider depending pmc-certificate-svc as well after device poll manager is done

Also created osal-network-boot oneshot service to start before the systemd-networkd since there is log with regards to Invalid Gateway in /lib/systemd/network/80-wired.network.d/80-wired.conf, but we're not touching that file
whats the super best move in here? because as you can see in the current, there are some json that is only supported in specific variant, but I think it's a little bit messy because as you can see, we do the checking runtime and not in the compilation.

can you give me options on how to improve it? like create different file for different vaiant? different function? I mean dont implement it yet, I need best idea


data-schema.c
#include "data-schema.h"
#include "device-profile.h"
#include "common.h"
#include "dbus-pub.h"
#include "helpers.h"

#include <stdio.h>
#include <string.h>
#include <json-c/json.h>

/** private variables */
/* none */

/** private constants */
static const char *epochFormat = "%Y-%m-%d %H:%M:%S";

/** private declarations */
static int publish_data(char *userdata);

static char arrSchemaStringBuff[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};
const char *printSchema2StringFormat(struct json_object *jsonObj)
{
    memset((void *)&arrSchemaStringBuff[0], 0, sizeof(arrSchemaStringBuff));

    (void)snprintf(&arrSchemaStringBuff[0], sizeof(arrSchemaStringBuff) - 1, "%s",
                   json_object_to_json_string_ext(jsonObj, (JSON_C_TO_STRING_SPACED | JSON_C_TO_STRING_PRETTY | JSON_C_TO_STRING_NOSLASHESCAPE)));

    return &arrSchemaStringBuff[0];
}

json_object *buildListSchema(device_data_st *ps_data, uint8_t u8_id)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "psu_id",
                           json_object_new_int((int32_t)u8_id));
    json_object_object_add(jsonBuffer, "device_address",
                           json_object_new_int((int32_t)(ps_data->u8_address)));
    

    if (true == ps_data->s_list.b_present)
    {
        json_object_object_add(jsonBuffer, "present",
                               json_object_new_boolean(1));
    }
    else
    {
        json_object_object_add(jsonBuffer, "present",
                               json_object_new_boolean(0));
    }

    json_object_object_add(jsonBuffer, "status",
                           json_object_new_string(generalStatus2String(ps_data->s_list.e_status)));

    return jsonBuffer;
}

json_object *buildConfigOtherSchema(config_data_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();
    json_object_object_add(jsonBuffer, "baudrate",
                           json_object_new_double(ps_data->s_others.u32_baudrate));
    return jsonBuffer;
}

json_object *buildConfigOverrideControlSchema(config_data_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "write_enable",
                           json_object_new_double((int)ps_data->s_controlOverride.b_writeEnable));
    json_object_object_add(jsonBuffer, "led_override_enable",
                           json_object_new_double((int)ps_data->s_controlOverride.b_ledOverrideEnable));
    json_object_object_add(jsonBuffer, "vout_override_enable",
                           json_object_new_double((int)ps_data->s_controlOverride.b_voutOverrideEnable));
    json_object_object_add(jsonBuffer, "reset_override_enable",
                           json_object_new_double((int)ps_data->s_controlOverride.b_resetOverrideEnable));

    return jsonBuffer;
}

json_object *buildConfigOverrideValuesSchema(config_data_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    if (MODULE_VARIANT_DLC != get_device_profile()->variant)
    {
        json_object_object_add(jsonBuffer, "fan_override",
                            json_object_new_double(ps_data->s_overrideValues.d_fanOverride));
        json_object_object_add(jsonBuffer, "power_led_override",
                            json_object_new_double(ps_data->s_overrideValues.d_powerLedOverride));
    }
    json_object_object_add(jsonBuffer, "led_override_enable",
                           json_object_new_double(ps_data->s_overrideValues.d_ledOverrideEnable));
    json_object_object_add(jsonBuffer, "status_led_override",
                           json_object_new_double(ps_data->s_overrideValues.d_statusLedOverride));
    json_object_object_add(jsonBuffer, "reset_pin_override",
                           json_object_new_double(ps_data->s_overrideValues.d_resetPinOverride));
    json_object_object_add(jsonBuffer, "vout_override",
                           json_object_new_double(ps_data->s_overrideValues.d_voutOverride));
    json_object_object_add(jsonBuffer, "vout_select",
                           json_object_new_double(ps_data->s_overrideValues.d_voutSelect));
    json_object_object_add(jsonBuffer, "vout_low_setpoint",
                           json_object_new_double(ps_data->s_overrideValues.d_voutLowSetpoint));
    json_object_object_add(jsonBuffer, "vout_high_setpoint",
                           json_object_new_double(ps_data->s_overrideValues.d_voutHighSetpoint));
    json_object_object_add(jsonBuffer, "vout_change_delay",
                           json_object_new_double(ps_data->s_overrideValues.d_voutChangeDelay));

    return jsonBuffer;
}

json_object *buildConfigInputSchema(config_data_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "vin_ov_fault",
                           json_object_new_double(ps_data->s_input.d_vinOvFault));
    json_object_object_add(jsonBuffer, "vin_uv_fault",
                           json_object_new_double(ps_data->s_input.d_vinUvFault));
    json_object_object_add(jsonBuffer, "freq_of_warning",
                           json_object_new_double(ps_data->s_input.d_freqOfWarning));
    json_object_object_add(jsonBuffer, "freq_uf_warning",
                           json_object_new_double(ps_data->s_input.d_freqUfWarning));

    return jsonBuffer;
}

json_object *buildConfigOutputSchema(config_data_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "vout_ov_fault",
                           json_object_new_double(ps_data->s_output.d_voutOvFault));
    json_object_object_add(jsonBuffer, "vout_uv_fault",
                           json_object_new_double(ps_data->s_output.d_voutUvFault));
    json_object_object_add(jsonBuffer, "iout_oc_fault",
                           json_object_new_double(ps_data->s_output.d_ioutOcFault));
    json_object_object_add(jsonBuffer, "pout_op_fault",
                           json_object_new_double(ps_data->s_output.d_poutOpFault));

    return jsonBuffer;
}

json_object *buildConfigTemperatureSchema(config_data_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "inlet_ot_fault",
                           json_object_new_double(ps_data->s_temperature.d_inletOtFault));
    json_object_object_add(jsonBuffer, "inlet_ot_warning",
                           json_object_new_double(ps_data->s_temperature.d_inletOtWarning));
    json_object_object_add(jsonBuffer, "inlet_ut_fault",
                           json_object_new_double(ps_data->s_temperature.d_inletUtFault));
    json_object_object_add(jsonBuffer, "outlet_ot_fault",
                           json_object_new_double(ps_data->s_temperature.d_outletOtFault));
    json_object_object_add(jsonBuffer, "outlet_ot_warning",
                           json_object_new_double(ps_data->s_temperature.d_outletOtWarning));
    json_object_object_add(jsonBuffer, "outlet_ut_fault",
                           json_object_new_double(ps_data->s_temperature.d_outletUtFault));
    json_object_object_add(jsonBuffer, "hotspot_ot_warning",
                           json_object_new_double(ps_data->s_temperature.d_hotspotOtWarning));

    return jsonBuffer;
}

json_object *buildConfigFanSchema(config_data_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "fan1_under_rpm_warning",
                           json_object_new_double(ps_data->s_fan.d_fan1UnderRpmWarning));
    json_object_object_add(jsonBuffer, "fan2_under_rpm_warning",
                           json_object_new_double(ps_data->s_fan.d_fan2UnderRpmWarning));

    return jsonBuffer;
}

#define WRITEABLE_CONFIG_LIST_COUNT         (12)
const char *s_writeableConfigList[WRITEABLE_CONFIG_LIST_COUNT] = 
{
    "baudrate",
    "fan_override",
    "power_led_override",
    "status_led_override",
    "vout_select",
    "vout_low_setpoint",
    "vout_high_setpoint",
    "vout_change_delay",
    "inlet_ot_fault",
    "inlet_ut_fault",
    "outlet_ot_fault",
    "outlet_ut_fault"
};

json_object *buildConfigSchema(device_data_st *ps_data, uint8_t u8_id)
{
    struct json_object *jsonBuffer;
    struct json_object *jsonTypeObj;
    struct json_object *jsonWriteableObj;

    jsonBuffer  = json_object_new_object();
    jsonTypeObj = json_object_new_object();

    json_object_object_add(jsonBuffer, "psu_id",
                           json_object_new_int((int32_t)u8_id));

    json_object_object_add(jsonTypeObj, "other", buildConfigOtherSchema(&ps_data->s_config));
    json_object_object_add(jsonTypeObj, "override_control", buildConfigOverrideControlSchema(&ps_data->s_config));
    json_object_object_add(jsonTypeObj, "override_values", buildConfigOverrideValuesSchema(&ps_data->s_config));
    json_object_object_add(jsonTypeObj, "input", buildConfigInputSchema(&ps_data->s_config));
    json_object_object_add(jsonTypeObj, "output", buildConfigOutputSchema(&ps_data->s_config));
    json_object_object_add(jsonTypeObj, "temperature", buildConfigTemperatureSchema(&ps_data->s_config));
    if (MODULE_VARIANT_DLC != get_device_profile()->variant)
    {
        json_object_object_add(jsonTypeObj, "fan", buildConfigFanSchema(&ps_data->s_config));
    }

    jsonWriteableObj    = json_object_new_array();
    for (uint8_t i = 0; i < WRITEABLE_CONFIG_LIST_COUNT; i++)
    {
        json_object_array_add(jsonWriteableObj, json_object_new_string(s_writeableConfigList[i]));
    }

    json_object_object_add(jsonBuffer, "type", jsonTypeObj);
    json_object_object_add(jsonBuffer, "writeable", jsonWriteableObj);

    return jsonBuffer;
}

json_object *buildStaticSchema(device_data_st *ps_data, uint8_t u8_id)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "psu_id",
                           json_object_new_int((int32_t)u8_id));
    json_object_object_add(jsonBuffer, "manufacturer",
                           json_object_new_string(&ps_data->s_static.s_manufacturer[0]));
    json_object_object_add(jsonBuffer, "part_number",
                           json_object_new_string(&ps_data->s_static.s_partNumber[0]));
    json_object_object_add(jsonBuffer, "serial_number",
                           json_object_new_string(&ps_data->s_static.s_serialNumber[0]));
    json_object_object_add(jsonBuffer, "manufacture_date",
                           json_object_new_string(&ps_data->s_static.s_manufactureDate[0]));
    json_object_object_add(jsonBuffer, "manufacture_location",
                           json_object_new_string(&ps_data->s_static.s_manufactureLocation[0]));
    json_object_object_add(jsonBuffer, "hw_version",
                           json_object_new_string(&ps_data->s_static.s_hwVersion[0]));
    json_object_object_add(jsonBuffer, "fw_version",
                           json_object_new_string(&ps_data->s_static.s_fwVersion[0]));

    json_object_object_add(jsonBuffer, "min_vin",
                           json_object_new_double(ps_data->s_static.d_minInputVoltage));
    json_object_object_add(jsonBuffer, "max_vin",
                           json_object_new_double(ps_data->s_static.d_maxInputVoltage));
    json_object_object_add(jsonBuffer, "max_iin",
                           json_object_new_double(ps_data->s_static.d_maxInputCurrent));
    json_object_object_add(jsonBuffer, "max_pin",
                           json_object_new_double(ps_data->s_static.d_maxInputPower));
    json_object_object_add(jsonBuffer, "min_vout",
                           json_object_new_double(ps_data->s_static.d_minOutputVoltage));
    json_object_object_add(jsonBuffer, "max_vout",
                           json_object_new_double(ps_data->s_static.d_maxOutputVoltage));
    json_object_object_add(jsonBuffer, "max_iout",
                           json_object_new_double(ps_data->s_static.d_maxOuputCurrent));
    json_object_object_add(jsonBuffer, "max_pout",
                           json_object_new_double(ps_data->s_static.d_maxOutputPower));
    json_object_object_add(jsonBuffer, "min_ambient_temp",
                           json_object_new_double(ps_data->s_static.d_minAmbientTemperature));
    json_object_object_add(jsonBuffer, "max_ambient_temp",
                           json_object_new_double(ps_data->s_static.d_maxAmbientTemperature));

    return jsonBuffer;
}

json_object *buildDynamicParamsSchema(device_data_st *ps_data, uint8_t u8_id)
{
    struct json_object *jsonBuffer;

    const device_profile_st *s_device_profile = get_device_profile();

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "psu_id",
                           json_object_new_int((int32_t)u8_id));

    json_object_object_add(jsonBuffer, "vin_volts",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_inputVoltage));
    json_object_object_add(jsonBuffer, "iin_amps",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_inputCurrent));
    json_object_object_add(jsonBuffer, "pin_watts",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_inputPower));
    json_object_object_add(jsonBuffer, "input_ac_freq_hz",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_inputAcFrequency));
    json_object_object_add(jsonBuffer, "ithd_percent",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_currentThd));
    json_object_object_add(jsonBuffer, "power_factor",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_powerFactor));

    json_object_object_add(jsonBuffer, "vout_volts",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_outputVoltage));
    json_object_object_add(jsonBuffer, "iout_amps",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_outputCurrent));
    json_object_object_add(jsonBuffer, "pout_watts",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_outputPower));

    json_object_object_add(jsonBuffer, "inlet_temp_celsius",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_inletTemperature));
    json_object_object_add(jsonBuffer, "outlet_temp_celsius",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_outletTemperature));

    if (MODULE_VARIANT_DLC != s_device_profile->variant)
    {
        json_object_object_add(jsonBuffer, "fan1_rpm",
                            json_object_new_double(ps_data->s_dynamic_readouts.d_fan1Rpm));
    }

    json_object_object_add(jsonBuffer, "ishare",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_currentShare));
    
    if (MODULE_VARIANT_HPR == s_device_profile->variant)
    {
        json_object_object_add(jsonBuffer, "pos_busbar_temp_celsius",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_posBusBarTemperature));
        json_object_object_add(jsonBuffer, "neg_busbar_temp_celsius",
                            json_object_new_double(ps_data->s_dynamic_readouts.d_negBusBarTemperature));
    }
    else if (MODULE_VARIANT_HPR_V2 == s_device_profile->variant)
    {
        json_object_object_add(jsonBuffer, "pos_busbar_temp_celsius",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_posBusBarTemperature));
        json_object_object_add(jsonBuffer, "neg_busbar_temp_celsius",
                            json_object_new_double(ps_data->s_dynamic_readouts.d_negBusBarTemperature));

        json_object_object_add(jsonBuffer, "fan2_rpm",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_fan2Rpm));
        json_object_object_add(jsonBuffer, "fan3_rpm",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_fan3Rpm));
        json_object_object_add(jsonBuffer, "fan4_rpm",
                           json_object_new_double(ps_data->s_dynamic_readouts.d_fan4Rpm));
        
    }

    return jsonBuffer;
}

json_object *buildDynamicStatusOtherSchema(dynamic_status_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();
    if (MODULE_VARIANT_DLC != get_device_profile()->variant)
    {
        json_object_object_add(jsonBuffer, "power_led",
                            json_object_new_string(generalStatus2String(ps_data->s_other.e_powerLed)));
    }
    json_object_object_add(jsonBuffer, "status_led",
                           json_object_new_string(generalStatus2String(ps_data->s_other.e_statusLed)));

    return jsonBuffer;
}

json_object *buildDynamicStatusSystemSchema(dynamic_status_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();
    json_object_object_add(jsonBuffer, "system_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_systemStatus)));
    json_object_object_add(jsonBuffer, "pfc_converter_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_pfcConverterStatus)));
    json_object_object_add(jsonBuffer, "main_converter_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_mainConverterStatus)));
    json_object_object_add(jsonBuffer, "communication_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_communicationStatus)));
    json_object_object_add(jsonBuffer, "dcdc_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_dcdcStatus)));
    json_object_object_add(jsonBuffer, "pfc_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_pfcStatus)));
    json_object_object_add(jsonBuffer, "logic_mcu_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_logicMcuFaultStatus)));
    json_object_object_add(jsonBuffer, "secondary_mcu_status",
                           json_object_new_string(generalStatus2String(ps_data->s_system.e_secondaryMcuFaultStatus)));

    return jsonBuffer;
}

json_object *buildDynamicStatusInputSchema(dynamic_status_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "vin_status",
                           json_object_new_string(generalStatus2String(ps_data->s_input.e_vinStatus)));

    json_object_object_add(jsonBuffer, "input_freq_status",
                           json_object_new_string(generalStatus2String(ps_data->s_input.e_inputFreqStatus)));

    return jsonBuffer;
}

json_object *buildDynamicStatusOutputSchema(dynamic_status_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "vout_status",
                           json_object_new_string(generalStatus2String(ps_data->s_output.e_voutStatus)));

    json_object_object_add(jsonBuffer, "iout_status",
                           json_object_new_string(generalStatus2String(ps_data->s_output.e_ioutStatus)));

    json_object_object_add(jsonBuffer, "pout_status",
                           json_object_new_string(generalStatus2String(ps_data->s_output.e_poutStatus)));

    return jsonBuffer;
}

json_object *buildDynamicStatusTemperatureSchema(dynamic_status_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "inlet_temp_status",
                           json_object_new_string(generalStatus2String(ps_data->s_temperature.e_inletTemperatureStatus)));

    json_object_object_add(jsonBuffer, "outlet_temp_status",
                           json_object_new_string(generalStatus2String(ps_data->s_temperature.e_outletTemperatureStatus)));

    json_object_object_add(jsonBuffer, "hotspot_temp_status",
                           json_object_new_string(generalStatus2String(ps_data->s_temperature.e_hotspotTemperatureStatus)));

    return jsonBuffer;
}

json_object *buildDynamicStatusFanSchema(dynamic_status_st *ps_data)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "fan1_status",
                           json_object_new_string(generalStatus2String(ps_data->s_fan.e_fan1Status)));

    json_object_object_add(jsonBuffer, "fan2_status",
                           json_object_new_string(generalStatus2String(ps_data->s_fan.e_fan2Status)));

    return jsonBuffer;
}

json_object *buildDynamicStatusSchema(device_data_st *ps_data, uint8_t u8_id)
{
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();

    json_object_object_add(jsonBuffer, "psu_id",
                           json_object_new_int((int32_t)u8_id));

    json_object_object_add(jsonBuffer, "other", buildDynamicStatusOtherSchema(&ps_data->s_dynamic_status));
    json_object_object_add(jsonBuffer, "system", buildDynamicStatusSystemSchema(&ps_data->s_dynamic_status));
    json_object_object_add(jsonBuffer, "input", buildDynamicStatusInputSchema(&ps_data->s_dynamic_status));
    json_object_object_add(jsonBuffer, "output", buildDynamicStatusOutputSchema(&ps_data->s_dynamic_status));
    json_object_object_add(jsonBuffer, "temperature", buildDynamicStatusTemperatureSchema(&ps_data->s_dynamic_status));
    if (MODULE_VARIANT_DLC != get_device_profile()->variant)
    {
        json_object_object_add(jsonBuffer, "fan", buildDynamicStatusFanSchema(&ps_data->s_dynamic_status));
    }

    return jsonBuffer;
}

int publish_list_update(uint8_t u8_id, device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};
    struct json_object *jsonRootObj;
    struct json_object *jsonListObj;

    struct json_object *jsonBuffer;

    jsonRootObj = json_object_new_object();
    json_object_object_add(jsonRootObj, "timestamp", json_object_new_string(epoch2DateTimeStringFormat(now, epochFormat)));
    json_object_object_add(jsonRootObj, "shelf_id", json_object_new_int(ps_data->u8_shelfId));
    
    jsonListObj = json_object_new_array();    
    jsonBuffer  = buildListSchema(ps_data, u8_id);

    json_object_array_add(jsonListObj, jsonBuffer);
    json_object_object_add(jsonRootObj, "list", jsonListObj);

    (void)snprintf(&buffer[0], sizeof(buffer) - 1, "%s",
                   printSchema2StringFormat(jsonRootObj));

    // ERROR_PRINT("Print Buffer %s \n",buffer);


    if (0 != json_object_array_length(jsonListObj))
    {
        r = publish_data(buffer);
    }
    else
    {
        r = 0;
    }

    json_object_put(jsonRootObj);

    return r;
}

int publish_list_update_all(device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};
    struct json_object *jsonRootObj;
    struct json_object *jsonListObj;

    struct json_object *jsonBuffer;

    jsonRootObj = json_object_new_object();
    json_object_object_add(jsonRootObj, "timestamp", json_object_new_string(epoch2DateTimeStringFormat(now, epochFormat)));
    json_object_object_add(jsonRootObj, "shelf_id", json_object_new_int(ps_data->u8_shelfId));
    
    jsonListObj = json_object_new_array();
    for (uint8_t i = 0; i < SUPPORTED_DEVICE_COUNT_DEFAULT; i++)
    {
        if(GENERAL_STATUS_ERROR != ps_data[i].s_list.e_status){
            jsonBuffer = buildListSchema(&ps_data[i], (i + 1));
            json_object_array_add(jsonListObj, jsonBuffer);
        }
    }
    
    json_object_object_add(jsonRootObj, "list", jsonListObj);

    (void)snprintf(&buffer[0], sizeof(buffer) - 1, "%s",
                   printSchema2StringFormat(jsonRootObj));

    // ERROR_PRINT("Print Buffer %s \n",buffer);

    if (0 != json_object_array_length(jsonListObj))
    {
        r = publish_data(buffer);
    }
    else
    {
        r = 0;
    }

    json_object_put(jsonRootObj);

    return r;
}

int publish_config_update(uint8_t u8_id, device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};
    struct json_object *jsonRootObj;
    struct json_object *jsonConfigObj;

    struct json_object *jsonBuffer;

    jsonRootObj = json_object_new_object();
    json_object_object_add(jsonRootObj, "timestamp", json_object_new_string(epoch2DateTimeStringFormat(now, epochFormat)));
    json_object_object_add(jsonRootObj, "shelf_id", json_object_new_int(ps_data->u8_shelfId));
    
    jsonConfigObj   = json_object_new_array();
    jsonBuffer      = buildConfigSchema(ps_data, u8_id);

    json_object_array_add(jsonConfigObj, jsonBuffer);
    json_object_object_add(jsonRootObj, "config", jsonConfigObj);

    (void)snprintf(&buffer[0], sizeof(buffer) - 1, "%s",
                   printSchema2StringFormat(jsonRootObj));

    if (0 != json_object_array_length(jsonConfigObj))
    {
        r = publish_data(buffer);
    }
    else
    {
        r = 0;
    }

    json_object_put(jsonRootObj);

    return r;
}

int publish_static_update(uint8_t u8_id, device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};
    struct json_object *jsonRootObj;
    struct json_object *jsonStaticObj;

    struct json_object *jsonBuffer;

    jsonRootObj = json_object_new_object();
    json_object_object_add(jsonRootObj, "timestamp", json_object_new_string(epoch2DateTimeStringFormat(now, epochFormat)));
    json_object_object_add(jsonRootObj, "shelf_id", json_object_new_int(ps_data->u8_shelfId));
    
    jsonStaticObj   = json_object_new_array();    
    jsonBuffer      = buildStaticSchema(ps_data, u8_id);

    json_object_array_add(jsonStaticObj, jsonBuffer);
    json_object_object_add(jsonRootObj, "static", jsonStaticObj);

    (void)snprintf(&buffer[0], sizeof(buffer) - 1, "%s",
                   printSchema2StringFormat(jsonRootObj));

    if (0 != json_object_array_length(jsonStaticObj))
    {
        r = publish_data(buffer);
    }
    else
    {
        r = 0;
    }

    json_object_put(jsonRootObj);

    return r;
}

int publish_dynamic_readouts_update_all(device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};

    struct json_object *jsonRootObj;
    struct json_object *jsonDynamicObj;

    struct json_object *jsonBuffer;

    jsonRootObj = json_object_new_object();
    json_object_object_add(jsonRootObj, "timestamp", json_object_new_string(epoch2DateTimeStringFormat(now, epochFormat)));
    json_object_object_add(jsonRootObj, "shelf_id", json_object_new_int(ps_data->u8_shelfId));

    jsonDynamicObj = json_object_new_array();
    for (uint8_t i = 0; i < SUPPORTED_DEVICE_COUNT_DEFAULT; i++)
    {
        if (DEVICE_STATUS_READY == ps_data[i].e_deviceStatus)
        {
            jsonBuffer = buildDynamicParamsSchema(&ps_data[i], (i + 1));
            json_object_array_add(jsonDynamicObj, jsonBuffer);
        }
    }

    json_object_object_add(jsonRootObj, "dynamic_reading", jsonDynamicObj);

    (void)snprintf(&buffer[0], sizeof(buffer) - 1, "%s",
                   printSchema2StringFormat(jsonRootObj));

    if (0 != json_object_array_length(jsonDynamicObj))
    {
        r = publish_data(buffer);
    }
    else
    {
        r = 0;
    }

    json_object_put(jsonRootObj);

    return r;
}




int publish_single_module_dynamic_readings(uint8_t u8_id, device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};

    struct json_object *jsonRootObj;
    struct json_object *jsonDynamicObj;

    struct json_object *jsonBuffer;

    jsonRootObj = json_object_new_object();
    json_object_object_add(jsonRootObj, "timestamp", json_object_new_string(epoch2DateTimeStringFormat(now, epochFormat)));
    json_object_object_add(jsonRootObj, "shelf_id", json_object_new_int(ps_data->u8_shelfId));

    jsonDynamicObj = json_object_new_array();
        
    jsonBuffer = buildDynamicParamsSchema(ps_data, u8_id);
    json_object_array_add(jsonDynamicObj, jsonBuffer);

    json_object_object_add(jsonRootObj, "dynamic_reading", jsonDynamicObj);

    (void)snprintf(&buffer[0], sizeof(buffer) - 1, "%s",
                   printSchema2StringFormat(jsonRootObj));

    if (0 != json_object_array_length(jsonDynamicObj))
    {
        r = publish_data(buffer);
    }
    else
    {
        r = 0;
    }

    json_object_put(jsonRootObj);

    return r;
}

static char g_lastDynamicStatusPayload[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};
int publish_dynamic_status_update_all(device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};

    struct json_object *jsonRootObj;
    struct json_object *jsonDynamicObj;
    struct json_object *jsonBuffer;

    const char *payloadStr = NULL;

    jsonDynamicObj = json_object_new_array();

    for (uint8_t i = 0; i < SUPPORTED_DEVICE_COUNT_DEFAULT; i++)
    {
        if (DEVICE_STATUS_READY == ps_data[i].e_deviceStatus)
        {
            jsonBuffer = buildDynamicStatusSchema(
                &ps_data[i],
                (i + 1));

            json_object_array_add(
                jsonDynamicObj,
                jsonBuffer);
        }
    }

    if (0 == json_object_array_length(jsonDynamicObj))
    {
        json_object_put(jsonDynamicObj);
        return 0;
    }

    payloadStr = json_object_to_json_string_ext(
        jsonDynamicObj,
        JSON_C_TO_STRING_PLAIN);

    if (0 == strcmp(g_lastDynamicStatusPayload, payloadStr))
    {
        json_object_put(jsonDynamicObj);

        // ERROR_PRINT(
        //     "Dynamic status unchanged, skip publish\n");

        return 0;
    }

    (void)snprintf(
        g_lastDynamicStatusPayload,
        sizeof(g_lastDynamicStatusPayload),
        "%s",
        payloadStr);

    jsonRootObj = json_object_new_object();

    json_object_object_add(
        jsonRootObj,
        "timestamp",
        json_object_new_string(
            epoch2DateTimeStringFormat(
                now,
                epochFormat)));

    json_object_object_add(
        jsonRootObj,
        "shelf_id",
        json_object_new_int(
            ps_data->u8_shelfId));

    json_object_object_add(
        jsonRootObj,
        "dynamic_status",
        jsonDynamicObj);

    (void)snprintf(
        &buffer[0],
        sizeof(buffer) - 1,
        "%s",
        printSchema2StringFormat(jsonRootObj));

    r = publish_data(buffer);

    json_object_put(jsonRootObj);

    return r;
}

int publish_single_module_dynamic_status(uint8_t u8_id, device_data_st *ps_data, time_t now)
{
    int r = -1;
    char buffer[STRING_BUFFER_SIZE_MAX_LIMIT] = {0};

    struct json_object *jsonRootObj;
    struct json_object *jsonDynamicObj;

    struct json_object *jsonBuffer;

    jsonRootObj = json_object_new_object();
    json_object_object_add(jsonRootObj, "timestamp", json_object_new_string(epoch2DateTimeStringFormat(now, epochFormat)));
    json_object_object_add(jsonRootObj, "shelf_id", json_object_new_int(ps_data->u8_shelfId));

    jsonDynamicObj = json_object_new_array();
        

    jsonBuffer = buildDynamicStatusSchema(ps_data, u8_id);
    json_object_array_add(jsonDynamicObj, jsonBuffer);

    json_object_object_add(jsonRootObj, "dynamic_status", jsonDynamicObj);

    (void)snprintf(&buffer[0], sizeof(buffer) - 1, "%s",
                   printSchema2StringFormat(jsonRootObj));

    if (0 != json_object_array_length(jsonDynamicObj))
    {
        r = publish_data(buffer);
    }
    else
    {
        r = 0;
    }

    json_object_put(jsonRootObj);

    return r;
}

static int publish_data(char *userdata)
{
    int r = -1;

    r = dbus_pub_push_data(PM_DBUS_PUB_INTERFACE,
                           PM_DBUS_PUB_OBJECT_PATH,
                           PM_DBUS_PUB_TOPIC,
                           userdata);

    return r;
}


take not that load_device_profile() is called in the main function and populates device profile
then
device-profile.c
const device_profile_st *get_device_profile(void)
{
    return &device_profile_s;
}

well there are some implementation in other repo doing like data-schema-std.c or data-schema-hpr/hpr-v2.c but I think there's a better approach?
variants are standard, hpr, hpr-v2 and dlc

