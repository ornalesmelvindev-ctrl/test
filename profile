this is a systemd module which polls for data through modbus

I really think overall, this can be simplified and optimized without losing the functionality, take note okay
this is already working, so heavy modification is not required(only if not necessary)

the problem here is, we are checking at some part of the source the module variant, as you notice, and what if we add another variant
specific feature? So add another section of if (variant == module_variant), like it will be so ugly in the future
what we want is to centralized everything, meaning load only in one place the device profile, for example in the main file
and let just the main file decide all, I mean we check too frequent the device profile
Like why not just create separate flow for each variant, of course dont duplicate the steps common for all, Like you will see
later there are some feature that is for MODULE_VARIANT_STANDARD only, for  MODULE_VARIANT_HPR, MODULE_VARIANT_HPR_V2 and some 
that are not supported by MODULE_VARIANT_DLC like fan(since dlc psu has no fan) and powerLED, MODULE_VARIANT_STANDARD input/output
power doesnt mean that DLC is the same since there will be condition later, we will just leave DLC blank for that

like in the main

main.c
#include <stdio.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <pthread.h>
#include <unistd.h>
#include <sys/stat.h>

#include "poll-manager.h"
#include "psu-detection.h"
#include "device-profile.h"
#include "data-schema.h"

#include "logs-client.h"
#include "dbus-actions.h"
#include "dbus-pub.h"
#include "helpers.h"
#include "config.h"
#include "ae-json.h"

/** private constants */
/* none */

/** private variables */
static device_data_st as_device_data[SUPPORTED_DEVICE_COUNT_DEFAULT] = {0};
static dynamic_status_st prev_dynamic_status[SUPPORTED_DEVICE_COUNT_DEFAULT] = {0};

static sd_bus_vtable vtable[] = 
{
    SD_BUS_VTABLE_START(0),

    SD_BUS_SIGNAL(PM_DBUS_PUB_TOPIC, "s", 0),
    SD_BUS_SIGNAL(PM_DBUS_LOGS_TOPIC, "s", 0),

    SD_BUS_VTABLE_END
};

static sd_bus_vtable action_vtable[] =
{
    SD_BUS_VTABLE_START(0),

    SD_BUS_METHOD("pollResource", "", "x", method_ResetPollState, SD_BUS_VTABLE_UNPRIVILEGED),
    // TODO: get-set property instead or get state method/property? all and PSUn
    SD_BUS_METHOD("idleStateResource", "", "x", method_IdlePollState, SD_BUS_VTABLE_UNPRIVILEGED),

    SD_BUS_VTABLE_END
};

static const device_profile_st *device_profile_s = NULL;

static action_objects_st s_action_objects = { 0 };

/** private declarations */
static void *poll_thread(void *args);
static result_code_et monitor_device(uint8_t u8_index, device_data_st *p_data, time_t now);
static uint16_t process_check_psu_presence(void);
static uint16_t process_check_psu_comms(void);
static void poll_device(uint8_t device_index, time_t now);
static void monitor_idle(void);
static void reset_state(void);

static bool is_idle_mode = false;

/** public definitions */
int main(int argc, char *argv[])
{
    int r;
    pthread_t thread_id;

    printf("Version: v%d.%d.%d.%d\n", VERSION_MAJOR, VERSION_MINOR, VERSION_FEATURE, VERSION_PATCH);

    r = dbus_svc_init();
    if (0 != r)
    {
        ERROR_PRINT("Failed to initialize D-Bus Service\n");
        exit(1);
    }

    r = pthread_create(&thread_id, NULL, &poll_thread, NULL);
    if (0 != r)
    {
        ERROR_PRINT("Cannot create new thread proccess!\n");
        exit(1);
    }

    /* add publisher */
    r = dbus_pub_register_object(PM_DBUS_PUB_INTERFACE,
                                 PM_DBUS_PUB_OBJECT_PATH,
                                 vtable,
                                 NULL);
    if (0 != r)
    {
        ERROR_PRINT("Failed to add D-Bus Publisher\n");
        exit(1);
    }

    r = dbus_action_add_method(PM_DBUS_ACTION_INTERFACE,
                               PM_DBUS_ACTION_OBJECT_PATH,
                               action_vtable,
                               &s_action_objects);
    if (0 != r)
    {
        ERROR_PRINT("Failed to add D-Bus Action\n");
    }

    if (0 == dbus_request_service(PM_DBUS_ACTION_SERVICE_NAME))
    {
        /* start dbus thread */
        dbus_svc_run_thread();
        dbus_svc_free();
    }
    else
    {
        ERROR_PRINT("Failed to request D-Bus Service name: %s\n", PM_DBUS_ACTION_SERVICE_NAME);
    }
    DEBUG_PRINT("Exiting: \n");
    return r < 0 ? EXIT_FAILURE : EXIT_SUCCESS;
}

/** private definitions */
static void *poll_thread(void *args)
{
    time_t now = time(NULL);
    time_t last_update;

    static uint16_t u16_module_bit_rep      = 0;
    static uint16_t u16_prev_module_bit_rep = 0;

    (void)args; //UNUSED

    if (0 != load_device_profile())
    {
        ERROR_PRINT("Failed to load device profile\n");
        return -1;
    }

    device_profile_s = get_device_profile();
    
    for (uint8_t i = 0; i < device_profile_s->supported_device_count; i++)
    {
        as_device_data[i].u8_shelfId = device_profile_s->shelf_id;
        as_device_data[i].u8_address = (device_profile_s->start_address + i);

        as_device_data[i].e_deviceStatus = DEVICE_STATUS_MISSING;

        init_dynamic_status(&prev_dynamic_status[i], GENERAL_STATUS_NORMAL);
    }

    /** get initial psu presence state */
    if ( MODULE_VARIANT_STANDARD == device_profile_s->variant)
    {
        (void)process_check_psu_comms();
    }
    u16_module_bit_rep      = process_check_psu_presence();
    u16_prev_module_bit_rep = u16_module_bit_rep;
    (void)publish_list_update_all(as_device_data, now);

    while (1)
    {
        bool b_resetPollState_snapshot = s_action_objects.b_resetPollState;
        if (true == s_action_objects.b_resetPollState)
        {
            reset_state();
        }
        else
        {
            monitor_idle();
        }

        // Perform polling of devices
        for (uint8_t i = 0; i < device_profile_s->supported_device_count; i++)
        {
            poll_device(i, now);
        }

        if ((true != is_idle_mode) && (true != b_resetPollState_snapshot))
        {
            u16_module_bit_rep = process_check_psu_presence();

            ProcessPSUPresenceLog(&u16_prev_module_bit_rep, u16_module_bit_rep);

            (void)publish_dynamic_readouts_update_all(as_device_data, now);
            (void)publish_dynamic_status_update_all(as_device_data, now);
        }
    
        last_update = time(NULL);

        while ((now - last_update) < DEVICE_POLL_INTERVAL)
        {
            nanosleep((const struct timespec[]){{0, 200000000L}}, NULL);
            now = time(NULL);
        }
    }
}

static result_code_et monitor_device(uint8_t u8_index, device_data_st *p_data, time_t now)
{
    result_code_et r = RESULT_CODE_ERROR;
    poll_state_et *p_state;

    p_state = &p_data->e_pollState;

    switch (*p_state)
    {
        case POLL_STATE_IDLE:
        {
            r = RESULT_CODE_OK;
            *p_state = POLL_STATE_IDLE;
            break;
        }

        case POLL_STATE_INIT:
        {
            memset(&p_data->s_list, 0, sizeof(list_data_st));
            memset(&p_data->s_config, 0, sizeof(config_data_st));

            memset(&p_data->s_static, 0, sizeof(static_data_st));
            memset(&p_data->s_dynamic_readouts, 0, sizeof(dynamic_readings_st));
            memset(&p_data->s_dynamic_status, 0, sizeof(dynamic_status_st));

            r = RESULT_CODE_OK;
            *p_state = POLL_STATE_LIST;
            break;
        }

        case POLL_STATE_LIST:
        {
            ERROR_PRINT("list: %02X\n", p_data->u8_address);
            if (0 == monitor_list(u8_index, p_data->u8_address, &p_data->s_list))
            {
                printf("Device with %02X address has been detected\n", p_data->u8_address);
                p_data->e_deviceStatus = DEVICE_STATUS_DETECTED;

                r = RESULT_CODE_OK;
                *p_state = POLL_STATE_STATIC;
            }
            break;
        }

        case POLL_STATE_STATIC:
        {
            ERROR_PRINT("static: %02X\n", p_data->u8_address);
            if (0 == monitor_static(u8_index,
                                    p_data->u8_address,
                                    &p_data->s_static))
            {
                (void)publish_static_update((u8_index + 1), p_data, now);

                r = RESULT_CODE_OK;
                *p_state = POLL_STATE_CONFIG;
            }
            break;
        }

        case POLL_STATE_CONFIG:
        {
            ERROR_PRINT("config: %02X\n", p_data->u8_address);
            if (0 == monitor_config(u8_index,
                                    p_data->u8_address,
                                    &p_data->s_config))
            {
                (void)publish_config_update((u8_index + 1), p_data, now);

                r = RESULT_CODE_OK;
                *p_state = POLL_STATE_DYNAMIC;
            }
            break;
        }

        case POLL_STATE_DYNAMIC:
        {
            ERROR_PRINT("dynamic: %02X\n", p_data->u8_address);
            if (0 != monitor_dynamic(u8_index,
                                    p_data->u8_address,
                                    &p_data->s_dynamic_readouts,
                                    &p_data->s_dynamic_status))
            {
                break;
            }

            if (DEVICE_STATUS_READY != p_data->e_deviceStatus)
            {
                if (0 == publish_list_update((u8_index + 1), p_data, now))
                {
                    p_data->e_deviceStatus = DEVICE_STATUS_READY;
                }
            }
            else
            {
                ProcessPSUFaultLogs(&p_data->s_dynamic_status, &prev_dynamic_status[u8_index], u8_index);
            }

            r = RESULT_CODE_OK;
            break;
        }

        default:
        {
            ERROR_PRINT("go back to init\n");

            *p_state = POLL_STATE_INIT;
            break;
        }
    }

    return r;
}

static uint16_t process_check_psu_presence(void)
{
    uint16_t module_bit_rep = 0;


    if ( (MODULE_VARIANT_HPR == device_profile_s->variant)
            || (MODULE_VARIANT_HPR_V2 == device_profile_s->variant)
        )
    {
        for (uint8_t i = 0; i < device_profile_s->supported_device_count; i++)
        {
            if (RESULT_CODE_OK == check_unit_presence(i))
            {
                module_bit_rep                      = (uint16_t)(module_bit_rep | 1 << i);
                as_device_data[i].s_list.b_present  = true;
            }
            else
            {
                as_device_data[i].s_list.b_present = false;
            }
        }
    }
    else // Standard
    {
        for (uint8_t i = 0; i < device_profile_s->supported_device_count; i++)
        {
            if (GENERAL_STATUS_OK == as_device_data[i].s_list.e_status)
            {
                module_bit_rep                      = (uint16_t)(module_bit_rep | 1 << i);
                as_device_data[i].s_list.b_present  = true;
            }
            else
            {
                as_device_data[i].s_list.b_present  = false;
            }
        }
    }

    return module_bit_rep;
}

static uint16_t process_check_psu_comms(void)
{
    uint16_t module_bit_rep = 0;

    for (uint8_t i = 0; i < device_profile_s->supported_device_count; i++)
    {   
        if (RESULT_CODE_OK == check_unit_comms(as_device_data[i].u8_address))
        {
            module_bit_rep                      = (uint16_t)(module_bit_rep | 1 << i);
            as_device_data[i].s_list.e_status   = GENERAL_STATUS_OK;
            
        }
        else
        {
            as_device_data[i].s_list.e_status   = GENERAL_STATUS_ERROR;            
        }
    }

    return module_bit_rep;
}

static void poll_device(uint8_t device_index, time_t now)
{
    if (RESULT_CODE_OK == monitor_device(device_index, &as_device_data[device_index], now) && as_device_data[device_index].s_list.b_present)
    {
        as_device_data[device_index].u8_retryCount = 0;
    }
    else
    {
        if ((DEVICE_ERROR_RESPONSE_RETRY_LIMIT > as_device_data[device_index].u8_retryCount)
            && (true == as_device_data[device_index].s_list.b_present)
        )
        {
            as_device_data[device_index].u8_retryCount++;
            ERROR_PRINT("Device with %02X retry count: %d.\n", as_device_data[device_index].u8_address,as_device_data[device_index].u8_retryCount);
        }
        else
        {
            if ( (as_device_data[device_index].e_deviceStatus == DEVICE_STATUS_DETECTED)
                || (as_device_data[device_index].e_deviceStatus == DEVICE_STATUS_READY)
            )
            {
                ERROR_PRINT("Device with %02X address has been disconnected.\n", as_device_data[device_index].u8_address);
                as_device_data[device_index].s_list.e_status    = GENERAL_STATUS_ERROR;
                as_device_data[device_index].e_pollState        = POLL_STATE_INIT;
                as_device_data[device_index].e_deviceStatus     = DEVICE_STATUS_LOST;
                (void)publish_list_update((device_index + 1), &as_device_data[device_index], now);

                (void)load_dynamic_disconnect_override(
                                            &as_device_data[device_index].s_dynamic_readouts,
                                            &as_device_data[device_index].s_dynamic_status
                                        );
                                        
                (void)publish_single_module_dynamic_status((device_index + 1), &as_device_data[device_index], now);
                (void)publish_single_module_dynamic_readings((device_index + 1), &as_device_data[device_index], now);
            }
        }
    }
}

static void monitor_idle(void)
{
    /* Idle Mode monitoring */
    if ((true == s_action_objects.b_idlePollState) && (false == is_idle_mode))
    {
        for (uint8_t i = 0; i < device_profile_s->supported_device_count; i++)
        {
            as_device_data[i].e_pollState       = POLL_STATE_IDLE;
            as_device_data[i].e_deviceStatus    = DEVICE_STATUS_IDLE;
        }

        is_idle_mode = true;
    }
}

static void reset_state(void)
{
    for (uint8_t i = 0; i < device_profile_s->supported_device_count; i++)
    {
        as_device_data[i].e_pollState = POLL_STATE_INIT;
    }

    s_action_objects.b_resetPollState   = false;
    s_action_objects.b_idlePollState    = false;

    is_idle_mode = false;
}

for now, we will focus in the monitor_dynamic, if the change seems to be okay and working without losing the functionality, then
    I will manually apply for the rest
monitor-dynamic.c
/** public definitions */
result_code_et monitor_dynamic(uint8_t u8_index, uint8_t u8_address,
                               dynamic_readings_st *ps_readings,
                               dynamic_status_st *ps_status)
{
    result_code_et r = RESULT_CODE_ERROR;

    r = monitor_dynamic_params(u8_index, u8_address, ps_readings, ps_status);

    return r;
}

static result_code_et monitor_dynamic_params(uint8_t u8_index, uint8_t u8_address,
                                             dynamic_readings_st *ps_readings,
                                             dynamic_status_st *ps_status)
{
    result_code_et r1 = RESULT_CODE_ERROR;
    result_code_et r2 = RESULT_CODE_ERROR;

#ifdef DEBUG_LOAD_DUMMY_DATA //TODO: move this in a function
    r1 = RESULT_CODE_OK;
    r2 = RESULT_CODE_OK;

    /** Readings */
    ps_readings->d_inputVoltage = 64.0;
    ps_readings->d_inputCurrent = 4.0;
    ps_readings->d_outputVoltage = 52.0;
    ps_readings->d_outputCurrent = 6.0;
    ps_readings->d_outputPower = 5000.0;

    ps_readings->d_inletTemperature = 32.0;
    ps_readings->d_outletTemperature = 36.0;
    ps_readings->d_chargerTemperature = 38.0;
    ps_readings->d_dischargerTemperature = 40.0;
    ps_readings->d_discharger2Temperature = 45.0;

    ps_readings->d_fan1Rpm = 90.0;
    ps_readings->d_faultCount = 30

    /** Status */
    ps_status->s_input.e_inputVoltage = GENERAL_STATUS_NORMAL;

    ps_status->s_output.e_outputVoltage = GENERAL_STATUS_OVER_FAULT;
    ps_status->s_output.e_outputCurrent = GENERAL_STATUS_UNDER_FAULT;

    ps_status->s_temperature.e_ambient = GENERAL_STATUS_NORMAL;
    ps_status->s_temperature.e_inlet = GENERAL_STATUS_OVER_FAULT;
    ps_status->s_temperature.e_outlet = GENERAL_STATUS_NORMAL;
    ps_status->s_temperature.e_charger = GENERAL_STATUS_UNDER_FAULT;
    ps_status->s_temperature.e_discharger = GENERAL_STATUS_OVER_FAULT;
    ps_status->s_temperature.e_discharger2 = GENERAL_STATUS_UNDER_FAULT;

    ps_status->s_fan.e_fan1 = GENERAL_STATUS_UNDER_FAULT;

    ps_status->s_others.e_powerLed = GENERAL_STATUS_ON;
    ps_status->s_others.e_statusLed = GENERAL_STATUS_OFF;
    ps_status->s_others.e_endOfLifeLed = GENERAL_STATUS_BLINKING;
    ps_status->s_others.e_lowVoltageLed = GENERAL_STATUS_BLINKING;

    ps_status->s_system.e_converterStatus = GENERAL_STATUS_NORMAL;
    ps_status->s_system.e_chargerStatus = GENERAL_STATUS_FAULT;

#else
    r1 = read_readouts_data(u8_index, u8_address, ps_readings);
    r2 = read_status_data(u8_index, u8_address, ps_status);
#endif

    return (((RESULT_CODE_OK == r1) && (RESULT_CODE_OK == r2)) ? RESULT_CODE_OK : RESULT_CODE_ERROR);
}

just a reminder, all is working fine okay? dont try to create or modify the functionality, or use different macros or offset modbus

static result_code_et read_readouts_data(uint8_t u8_index, uint8_t u8_address, dynamic_readings_st *ps_data)
{
    result_code_et r = RESULT_CODE_ERROR;
    uint16_t u16_buffer[MODBUS_MAX_REGISTER_READ_COUNT] = {0};
    uint16_t u16_data = 0x0000;
    uint16_t u16_startRegister = 0x0000;
    uint8_t u8_registerCount = 0;
    uint16_t u16_registerOffset = 0;

    const device_profile_st *s_device_profile = get_device_profile();

    u16_startRegister = MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS;  // 0x3C
    u16_registerOffset = MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS; // 0x3C
    u8_registerCount = ((MODBUS_REGISTER_OFFSET_LED_OVERRIDE - MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS) //(0x61 - 0x3C) + 1
                            + MODBUS_REGISTER_OFFSET_LED_OVERRIDE_WORD_SIZE);                                   //(End register - Start register) + End Register Size. Sum is 0x26
    
    if (0 == readModbusRegisters(u8_address,
                                 u16_startRegister,
                                 u8_registerCount,
                                 &u16_buffer[0]))
    {
        /** copy modbus data into register map */
        for (uint8_t i = 0; i < u8_registerCount; i++)
        {
            (void)set_modbus_register(u8_index, (u16_registerOffset + i), u16_buffer[i]);
        }

        /** read outs */
        /** input voltage */
        ps_data->d_inputVoltage = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_VOLTAGE - u16_registerOffset],
                                                         6,
                                                         DATA_FORMAT_UINT16);
        ps_data->d_inputVoltage = limit2Precision(ps_data->d_inputVoltage);

        /** input current */
        ps_data->d_inputCurrent = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_CURRENT - u16_registerOffset],
                                                        10,
                                                        DATA_FORMAT_UINT16);
        ps_data->d_inputCurrent = limit2Precision(ps_data->d_inputCurrent);
        
        /** input ac frequency */
        ps_data->d_inputAcFrequency = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_FREQUENCY - u16_registerOffset],
                                                             0,
                                                             DATA_FORMAT_INT16);
        ps_data->d_inputAcFrequency = limit2Precision(ps_data->d_inputAcFrequency);

        /** current thd */
        ps_data->d_currentThd = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_CURRENT_THD - u16_registerOffset],
                                                       9,
                                                       DATA_FORMAT_INT16);
        ps_data->d_currentThd = limit2Precision(ps_data->d_currentThd);

        /** power factor */
        ps_data->d_powerFactor = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_POWER_FACTOR - u16_registerOffset],
                                                        9,
                                                        DATA_FORMAT_INT16);
        ps_data->d_powerFactor = limit2Precision(ps_data->d_powerFactor);

        /** output voltage */
        ps_data->d_outputVoltage = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTPUT_VOLTAGE - u16_registerOffset],
                                                          10,
                                                          DATA_FORMAT_UINT16);
        ps_data->d_outputVoltage = limit2Precision(ps_data->d_outputVoltage);

        /** output current */
        ps_data->d_outputCurrent = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTPUT_CURRENT - u16_registerOffset],
                                                          6,
                                                          DATA_FORMAT_UINT16);
        ps_data->d_outputCurrent = limit2Precision(ps_data->d_outputCurrent);

        /** temperatures */
        ps_data->d_inletTemperature = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INLET_TEMPERATURE - u16_registerOffset],
                                                             7,
                                                             DATA_FORMAT_INT16);
        ps_data->d_inletTemperature = limit2Precision(ps_data->d_inletTemperature);

        ps_data->d_outletTemperature = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTLET_TEMPERATURE - u16_registerOffset],
                                                              7,
                                                              DATA_FORMAT_INT16);
        ps_data->d_outletTemperature = limit2Precision(ps_data->d_outletTemperature);

        /** current share */
        ps_data->d_currentShare = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_ISHARE_CURRENT],
                                                         6,
                                                         DATA_FORMAT_INT16);
        ps_data->d_currentShare = limit2Precision(ps_data->d_currentShare);
        
        if (MODULE_VARIANT_DLC != s_device_profile->variant)
        {
            /** fan */
            ps_data->d_fan1Rpm = (double)(u16_buffer[MODBUS_REGISTER_OFFSET_RPM_FAN_0 - u16_registerOffset]);
            ps_data->d_fan1Rpm = limit2Precision(ps_data->d_fan1Rpm);
        }

        if ( (MODULE_VARIANT_HPR == s_device_profile->variant)
                || (MODULE_VARIANT_HPR_V2 == s_device_profile->variant)
        )
        {
            r = process_readouts_data_hpr(u8_index, u8_address, ps_data);
        }
        else
        {
            /** input power */
            ps_data->d_inputPower = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_INPUT_POWER - u16_registerOffset],
                                                          3,
                                                          DATA_FORMAT_UINT16);
            ps_data->d_inputPower = limit2Precision(ps_data->d_inputPower);

            /** output power */
            ps_data->d_outputPower = convertHalfWord2Double(u16_buffer[MODBUS_REGISTER_OFFSET_OUTPUT_POWER - u16_registerOffset],
                                                            3,
                                                            DATA_FORMAT_UINT16);
            ps_data->d_outputPower = limit2Precision(ps_data->d_outputPower);

            r = RESULT_CODE_OK;
        }        
    }

    return r;
}

static result_code_et process_readouts_data_hpr(uint8_t u8_index, uint8_t u8_address, dynamic_readings_st *ps_data)
{
    result_code_et r = RESULT_CODE_ERROR;
    uint16_t u16_buffer[MODBUS_MAX_REGISTER_READ_COUNT] = {0};
    uint16_t u16_data = 0x0000;
    uint16_t u16_startRegister = 0x0000;
    uint8_t u8_registerCount = 0;
    uint16_t u16_registerOffset = 0;
    uint8_t u8_arrayOffset      = 0;

    const device_profile_st *s_device_profile = get_device_profile();

    if ( MODULE_VARIANT_HPR_V2 == s_device_profile->variant )
    {
        u16_startRegister   = MODBUS_REGISTER_OFFSET_RPM_FAN_1; // 0x44
        u16_registerOffset  = MODBUS_REGISTER_OFFSET_RPM_FAN_1; // 0x44
        u8_registerCount    = ((MODBUS_REGISTER_OFFSET_HPR_V2_INPUT_POWER_AVERAGE - MODBUS_REGISTER_OFFSET_RPM_FAN_1) //(0x92 - 0x44) + 1
                                + MODBUS_REGISTER_OFFSET_HPR_V2_INPUT_POWER_AVERAGE_WORD_SIZE);                       //(End register - Start register) + End Register Size. Sum is 0x4F
    }
    else
    {
        u16_startRegister   = MODBUS_REGISTER_OFFSET_OUTPUT_POWER; // 0x52
        u16_registerOffset  = MODBUS_REGISTER_OFFSET_OUTPUT_POWER; // 0x52
        u8_registerCount    = ((MODBUS_REGISTER_OFFSET_NEG_BUS_BAR_TEMPERATURE - MODBUS_REGISTER_OFFSET_OUTPUT_POWER) //(0x70 - 0x52) + 1
                                + MODBUS_REGISTER_OFFSET_NEG_BUS_BAR_TEMPERATURE_WORD_SIZE);                          //(End register - Start register) + End Register Size. Sum is 0x26
    }

    assert(u8_registerCount <= MODBUS_MAX_REGISTER_READ_COUNT);

    if (0 == readModbusRegisters(u8_address,
                            u16_startRegister,
                            u8_registerCount,
                            &u16_buffer[0]))
    {
        /** copy modbus data into register map */
        for (uint8_t i = 0; i < u8_registerCount; i++)
        {
            (void)set_modbus_register(u8_index, (u16_registerOffset + i), u16_buffer[i]);
        }

        /** bus bar temperature */
        u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_POS_BUS_BAR_TEMPERATURE - u16_registerOffset);
        ps_data->d_posBusBarTemperature = convertHalfWord2Double(u16_buffer[u8_arrayOffset],
                                                                    7,
                                                                    DATA_FORMAT_INT16);
        ps_data->d_posBusBarTemperature = limit2Precision(ps_data->d_posBusBarTemperature);

        u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_NEG_BUS_BAR_TEMPERATURE - u16_registerOffset);
        ps_data->d_negBusBarTemperature = convertHalfWord2Double(u16_buffer[u8_arrayOffset],
                                                                    7,
                                                                    DATA_FORMAT_INT16);
        ps_data->d_negBusBarTemperature = limit2Precision(ps_data->d_negBusBarTemperature);
        
        if (MODULE_VARIANT_HPR_V2 == s_device_profile->variant)
        {
            /** fans */
            u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_RPM_FAN_1 - u16_registerOffset);
            ps_data->d_fan2Rpm = (double)(u16_buffer[u8_arrayOffset]);
            ps_data->d_fan2Rpm = limit2Precision(ps_data->d_fan2Rpm);

            u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_RPM_FAN_2 - u16_registerOffset);
            ps_data->d_fan3Rpm = (double)(u16_buffer[u8_arrayOffset]);
            ps_data->d_fan3Rpm = limit2Precision(ps_data->d_fan3Rpm);

            u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_RPM_FAN_3 - u16_registerOffset);
            ps_data->d_fan4Rpm = (double)(u16_buffer[u8_arrayOffset]);
            ps_data->d_fan4Rpm = limit2Precision(ps_data->d_fan4Rpm);

            /** input power */
            u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_HPR_V2_INPUT_POWER_AVERAGE - u16_registerOffset);
            ps_data->d_inputPower = convertHalfWord2Double(u16_buffer[u8_arrayOffset],
                                                        1,
                                                        DATA_FORMAT_UINT16);
            ps_data->d_inputPower = limit2Precision(ps_data->d_inputPower);

            /** output power */
            u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_HPR_V2_OUTPUT_POWER - u16_registerOffset);
            ps_data->d_outputPower = convertHalfWord2Double(u16_buffer[u8_arrayOffset],
                                                            1,
                                                            DATA_FORMAT_UINT16);
            ps_data->d_outputPower = limit2Precision(ps_data->d_outputPower);
        }
        else
        {
            /** input power */
            u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_INPUT_POWER - u16_registerOffset);
            ps_data->d_inputPower = convertHalfWord2Double(u16_buffer[u8_arrayOffset],
                                                          3,
                                                          DATA_FORMAT_UINT16);
            ps_data->d_inputPower = limit2Precision(ps_data->d_inputPower);

            /** output power */
            u8_arrayOffset = (uint8_t)(MODBUS_REGISTER_OFFSET_OUTPUT_POWER - u16_registerOffset);
            ps_data->d_outputPower = convertHalfWord2Double(u16_buffer[u8_arrayOffset],
                                                            3,
                                                            DATA_FORMAT_UINT16);
            ps_data->d_outputPower = limit2Precision(ps_data->d_outputPower);
        }

        r = RESULT_CODE_OK;
    }

    return r;
}


Also our main goal here is scalability, readability(be generous on naming macros, variables or functions, readable and understandable enough)
maintainability, and consideration for the other developers


and here, the step for generation of the json string tp be emitted
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


I mean for now, I will let you decide the best approach in here, but if that doesnt fit my taste, I can give you what we planned


for your reference, these is the device profile details
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
