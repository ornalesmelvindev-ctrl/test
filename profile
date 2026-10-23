wait, I will give you the full implementation of monitor_dynamic, will focus first in here because I can apply
the changes in here to the rest if it seems worth

This must be full working implementation of monitor_dynamic okay?

now, you can give me separate files and just send me as tarball

starting in the main.c
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

static result_code_et read_status_data(uint8_t u8_index, uint8_t u8_address, dynamic_status_st *ps_data)
{
    result_code_et r = RESULT_CODE_ERROR;
    uint16_t u16_buffer[MODBUS_MAX_REGISTER_READ_COUNT] = {0};
    uint16_t u16_data = 0x0000;
    uint16_t u16_ledData = 0x0000;
    uint16_t u16_startRegister = 0x0000;
    uint8_t u8_registerCount = 0;
    uint16_t u16_registerOffset = 0;

    uint16_t u16_generalAlarmBuffer = 0;
    uint16_t u16_pfcAlarmBuffer = 0;
    uint16_t u16_dcdcAlarmBuffer = 0;
    uint16_t u16_tempAlarmBuffer = 0;
    uint16_t u16_commsAlarmBuffer = 0;
    uint16_t u16_ledOverrideBuffer = 0;

    u16_startRegister = MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS;                                                                                          // 0x3C
    u16_registerOffset = MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS;                                                                                         // 0x3C
    u8_registerCount = ((MODBUS_REGISTER_OFFSET_LED_OVERRIDE - MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS) + MODBUS_REGISTER_OFFSET_LED_OVERRIDE_WORD_SIZE); //(End register - Start register) + End Register Size. Sum is 26

    if (0 != readModbusRegisters(u8_address,
                                 u16_startRegister,
                                 u8_registerCount,
                                 &u16_buffer[0]))
    {
        return r;
    }
    /** copy modbus data into register map */
    for (uint8_t i = 0; i < u8_registerCount; i++) // 0x26
    {
        (void)set_modbus_register(u8_index, (u16_registerOffset + i), u16_buffer[i]);
    }
    if (MODULE_VARIANT_DLC != get_device_profile()->variant)
    {
        if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_LED_OVERRIDE, &u16_ledOverrideBuffer))
        {
            setPowerStatusLed(u16_ledOverrideBuffer, ps_data);
        }
    }

    if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_GENERAL_ALARM_STATUS, &u16_generalAlarmBuffer))
    {
        setGeneralAlarm(u16_generalAlarmBuffer, ps_data);
    }

    if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_PFC_ALARM_STATUS, &u16_pfcAlarmBuffer))
    {
        setPfcAlarm(u16_pfcAlarmBuffer, ps_data);
    }

    if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_DCDC_ALARM_STATUS, &u16_dcdcAlarmBuffer))
    {
        setDcdcAlarm(u16_dcdcAlarmBuffer, ps_data);
    }

    if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_TEMPERATURE_ALARM_STATUS, &u16_tempAlarmBuffer))
    {
        setTempAlarm(u16_tempAlarmBuffer, ps_data);
    }

    if (0 == get_modbus_register(u8_index, MODBUS_REGISTER_OFFSET_COMMUNICATION_ALARM_STATUS, &u16_commsAlarmBuffer))
    {
        setCommsAlarm(u16_commsAlarmBuffer, ps_data);
    }

    if (!(u16_generalAlarmBuffer & MODBUS_GENERAL_ALARM_BIT_COMMUNICATION))
    {
        if (u16_commsAlarmBuffer & (MODBUS_COMMS_ALARM_BIT_PRIMARY_MCU_FAULT | MODBUS_COMMS_ALARM_BIT_SECONDARY_LOGIC_FAULT))
        {
            ps_data->s_system.e_communicationStatus = GENERAL_STATUS_FAULT;
        }
        else
        {
            ps_data->s_system.e_communicationStatus = GENERAL_STATUS_NORMAL;
        }
    }
    else
    {
        ps_data->s_system.e_communicationStatus = GENERAL_STATUS_NORMAL;
    }

    r = RESULT_CODE_OK;

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

then the one that creates the json string to be emitted
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


