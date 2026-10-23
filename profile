this is in the main file
while(1)
    .
    .
    .
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

because our goal here is, the variant checking should only be in the main file, right now

monitor-list.c
/** public definitions */
result_code_et monitor_list(uint8_t u8_index, uint8_t u8_address, list_data_st *ps_data)
{
    result_code_et e_presence       = RESULT_CODE_ERROR;
    result_code_et e_comms_status   = RESULT_CODE_ERROR;

#ifdef DEBUG_LOAD_DUMMY_DATA
    e_presence      = RESULT_CODE_OK;
    e_comms_status  = RESULT_CODE_OK;
#else
    const device_profile_st *s_device_profile = get_device_profile();

    if ( (MODULE_VARIANT_HPR == s_device_profile->variant)
            || (MODULE_VARIANT_HPR_V2 == s_device_profile->variant)
        )
    {
        e_presence = check_unit_presence(u8_index);
    }
    else /** ORV Standard */
    {
        e_presence = RESULT_CODE_OK;
    }
    
    if (RESULT_CODE_OK == e_presence)
    {
        e_comms_status = check_unit_comms(u8_address);
    }
#endif

    /** presence status */
    if (RESULT_CODE_OK == e_presence)
    {   
        ps_data->b_present  = true;       
    }
    else
    {
        ps_data->b_present  = false;
    }

    /** comms status */
    if (RESULT_CODE_OK == e_comms_status)
    {   
        ps_data->e_status   = GENERAL_STATUS_OK;        
    }
    else
    {
        ps_data->e_status   = GENERAL_STATUS_ERROR;
    }

    return (((RESULT_CODE_OK == e_presence) && (RESULT_CODE_OK == e_comms_status)) ? RESULT_CODE_OK : RESULT_CODE_ERROR);
}

then next state 
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
monitor-dynamic.c
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
      r1 = read_readouts_data(u8_index, u8_address, ps_readings);
    r2 = read_status_data(u8_index, u8_address, ps_status);

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

like our initial plan is to first, set the common for all variants for example, then check in the main file like for example
if variant == MODULE_VARIANT_HPR_V2
    call a function that populate what needs to be populated

    like the variant specific features


as I told you, same goes in writing the json to be emitted
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


whts the best move in here? because our problem in here is when we add variant specific features in the future, then we will
again add if(variant ...) ....
