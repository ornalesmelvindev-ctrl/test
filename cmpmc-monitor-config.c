#define _XOPEN_SOURCE
#define _GNU_SOURCE 

#include "monitor-config.h"
#include "config-manager.h"
#include "common.h"
#include "dbus-pub.h"
#include "logs-client.h"
#include "helpers.h"
#include "queue.h"

#include "osal_network.h"

#include <string.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdio.h>
#include <json-c/json.h>
#include <sqlite3.h>
#include <unistd.h>
#include <time.h>

#ifdef ENABLE_TEST_HOOKS
#include <sanitizer/lsan_interface.h>
#endif



/* Private Constants */
static const char CALL_POLL_RESOURCE[] =
#ifndef ENABLE_TEST_HOOKS
    "busctl call aei.mc.pollmanager1 /aei/mc/pollmanager1 aei.mc.pollmanager1.actions pollResource | awk '{print $2;}'";
#else
    "echo 0";
#endif /* ENABLE_TEST_HOOKS */

static const char SET_HOSTNAME_SCRIPT[] =
    "set_hostname.sh";

static const char SET_STATIC_IP_SCRIPT[] =
    "set_static_ip.sh";

static const char SET_STATIC_IPV6_SCRIPT[] =
    "set_static_ipv6.sh";

static const char GET_STATIC_IP_SCRIPT[] =
    "get_static_ip.sh | grep 'ip' | awk '{print $2}' | cut -d'/' -f1 | cut -d',' -f1";

static const char SET_NTP_ENABLE_SCRIPT[] =
    "enable_ntp.sh";

static const char SET_NTP_DISABLE_SCRIPT[] =
    "disable_ntp.sh";

static const char GET_NTP_STATUS_SCRIPT[] =
    "get_ntp_status.sh | awk '{print $2}'";

static const char SET_NTP_REPLACE_SERVER_SCRIPT[] =
    "replace_ntp_servers.sh";

static const char READ_NTP_SERVER_SCRIPT[] =
    "read_ntp_servers.sh | cut -d' ' -f2-";

static const char SET_SSH_ENABLE_SCRIPT[] =
    "enable_ssh.sh";

static const char SET_SSH_DISABLE_SCRIPT[] =
    "disable_ssh.sh";

static const char GET_SSH_STATUS_SCRIPT[] =
    "get_ssh_status.sh | awk '{print $2}'";

static const char SET_DHCP_ENABLE_SCRIPT[] =
    "enable_dhcp.sh";

static const char SET_DHCP_DISABLE_SCRIPT[] =
    "disable_dhcp.sh";

static const char GET_DHCP_STATUS_SCRIPT[] =
    "get_dhcp_status.sh | awk '{print $2}'";

static const char SET_HTTP_ENABLE_SCRIPT[] =
    "enable_http.sh";

static const char SET_HTTP_DISABLE_SCRIPT[] =
    "disable_http.sh";

static const char GET_HTTP_STATUS_SCRIPT[] =
    "get_http_status.sh | awk '{print $2}'";

static const char SET_DNS_ENABLE_SCRIPT[] =
    "enable_dns.sh";

static const char SET_DNS_DISABLE_SCRIPT[] =
    "disable_dns.sh";

static const char GET_DNS_STATUS_SCRIPT[] =
    "get_dns_status.sh | awk '{print $2}'";

static const char SET_DNS_REPLACE_SERVER_SCRIPT[] =
    "replace_dns_servers.sh";

static const char READ_DNS_SERVER_SCRIPT[] =
    "read_dns_servers.sh | cut -d' ' -f2-";

static const char READ_SYSTEM_TIME[] =
    "get_time.sh | cut -d' ' -f2- | awk -F':' '{print $1}'";

static const char SET_SYSTEM_TIME[] =
    "set_time.sh";

typedef enum
{
    SHELF_TYPE_UNKNOWN = (-1),
    SHELF_TYPE_PSU = 0,
    SHELF_TYPE_BBU = 1

    ,
    SHELF_TYPE_COUNT
} shelf_type_et;

/* private variables */
static shelf_type_et e_shelfType = SHELF_TYPE_UNKNOWN;
static const char *s_shelfDbFile[SHELF_TYPE_COUNT] =
    {
        PSU_DB_FILE,
        BBU_DB_FILE};

/** private declarations */
static int processConfigPhysicalComms(uint8_t u8_managerId, struct json_object *jsonObj);
static int processConfigEthernet(struct json_object *jsonObj, uint8_t u8_managerId);
static int processConfigNetwork(uint8_t u8_managerId, struct json_object *jsonObj);
static int processConfigNetworkSsh(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled);
static int processConfigNetworkDhcp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled);
static int processConfigNetworkHttp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled);
static int processConfigNetworkDns(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled);
static int processConfigNetworkNtp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled);
static int processConfigNetworkSsdp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled);
static int proccessSystemTime(uint8_t u8_managerId, struct json_object *jsonTImeObj);


static int updateConfigNetwork(struct json_object *jsonBuffer);
static int updateConfigNetworkSsh(struct json_object *jsonBuffer);
static int updateConfigNetworkDhcp(struct json_object *jsonBuffer);
static int updateConfigNetworkHttp(struct json_object *jsonBuffer);
static int updateConfigNetworkDnsEnabledCheck(struct json_object *jsonBuffer, char *enabled);
static int updateConfigNetworkDnsConfigCheck(struct json_object *jsonBuffer, char *enabled);
static int updateConfigNetworkDns(struct json_object *jsonBuffer);
static int updateConfigNetworkNtpEnabledCheck(struct json_object *jsonBuffer, char *enabled);
static int updateConfigNetworkNtpConfigCheck(struct json_object *jsonBuffer, char *enabled);
static int updateConfigNetworkNtp(struct json_object *jsonBuffer);
static int updateConfigEthernet(struct json_object *jsonBuffer);
static int updateConfigEthernetStaticIp(struct json_object *jsonBuffer);
static int updateConfigEthernetStaticIpv6(struct json_object *jsonBuffer);
static int updateSystemTime(struct json_object *jsonTimeObj);

static int pushConfigBuffer(struct json_object *jsonBuffer);
static int callPollResource(void);

static shelf_type_et get_shelf_type(uint8_t u8_managerId);
static bool is_config_writeable(const char *dbFile, char *table, uint8_t u8_managerId, char *protocol);
static bool is_config_physical_comms_writeable(const char *dbFile, char *table, uint8_t u8_managerId, char *type);

/** public definitions */

int init_monitor(void)
{
    int r = -1; // originally 0. changed to have default error return value

    e_shelfType = get_shelf_type(MASTER_SHELF_ID);

    if (SHELF_TYPE_UNKNOWN != e_shelfType)
    {
        r = 0;
    }

    return r;
}

int peek_updates(uint8_t u8_managerId)
{
    int r = -1;
    char *cfgBuffer;
    char mgrBuffer[STRING_BUFFER_SIZE_S] = {0};
    int num_write = 0;

    num_write = snprintf(&mgrBuffer[0], sizeof(mgrBuffer), "\"manager_id\": %d,", u8_managerId);
    if (num_write >= sizeof(mgrBuffer))
    {
        printf("Value stored in mgrBuffer has been truncated.\n");
    }

    cfgBuffer = peek_queue();

    if (NULL != cfgBuffer)
    {
        if (strstr(cfgBuffer, mgrBuffer))
        {
            r = 0;
        }
        else
        {
            /* do nothing */
        }
    }

    return r;
}

int relay_updates(void)
{
    int r = -1;
    char *jsonBuffer = NULL;
    struct json_object *jsonObj = NULL;
    struct json_object *jsonEthernet = NULL;
    struct json_object *jsonSerial = NULL;
    struct json_object *jsonNetwork = NULL;
    struct json_object *jsonManagerId = NULL;
    struct json_object *systemTime = NULL;


    jsonBuffer = dequeue();

    if (NULL != jsonBuffer)
    {
        jsonObj = json_tokener_parse(jsonBuffer);
        (void)json_object_object_get_ex(jsonObj, "manager_id", &jsonManagerId);

        // Get system_time
        if (true == json_object_object_get_ex(jsonObj, "system_time", &systemTime))
        {
            r = updateSystemTime(systemTime);
        }

        if (strstr(jsonBuffer, "ethernet"))
        {
            (void)json_object_object_get_ex(jsonObj, "ethernet", &jsonEthernet);
            r = updateConfigEthernet(jsonEthernet);
        }
        else if (strstr(jsonBuffer, "serial"))
        {
            (void)json_object_object_get_ex(jsonObj, "serial", &jsonSerial);
        }
        else if (strstr(jsonBuffer, "network"))
        {
            (void)json_object_object_get_ex(jsonObj, "network", &jsonNetwork);
            r = updateConfigNetwork(jsonNetwork);
        }
        else
        {
            /* do nothing */
        }

        json_object_put(jsonObj);
    }

    if (0 == r)
    {
        /* 1st Attempt of calling poll resource method. Retry if failed. */
        if (0 != callPollResource())
        {
            /* 2nd Attempt of calling poll resource method */
            if (0 != callPollResource())
            {
                (void)ProcessPmcPollResourceFaultLogs();
            }
            else
            {
                /* do nothing */
            }
        }
        else
        {
            /* do nothing */
        }
    }

    return r;
}

static int updateConfigEthernet(struct json_object *jsonBuffer)
{
    int r = -1;
    struct json_object *jsonPortName = NULL;
    struct json_object *jsonHostName = NULL;
    struct json_object *jsonStaticIp = NULL;
    struct json_object *jsonIpv6Static = NULL;

    (void)json_object_object_get_ex(jsonBuffer, "port_name", &jsonPortName);
    (void)json_object_object_get_ex(jsonBuffer, "hostname", &jsonHostName);
    (void)json_object_object_get_ex(jsonBuffer, "ipv4_static", &jsonStaticIp);
    (void)json_object_object_get_ex(jsonBuffer, "ipv6_static", &jsonIpv6Static);

    // TODO: fix this to accommodate other eth ports
    if (0 == strcmp("ETH0", json_object_get_string(jsonPortName)))
    {
        if(NULL != jsonStaticIp)
        {
            r = updateConfigEthernetStaticIp(jsonStaticIp);
        }

        if (NULL != jsonIpv6Static)
        {
            r = updateConfigEthernetStaticIpv6(jsonIpv6Static);
        }
    }
    return r;
}

static int updateConfigEthernetStaticIp(struct json_object *jsonBuffer)
{
    int r = -1;
    struct json_object *jsonIpAddress = NULL;
    struct json_object *jsonSubnetMask = NULL;
    struct json_object *jsonGatewayAddress = NULL;
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strbuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strcmpBuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char ipAddr[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char subMask[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char gwAddr[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    uint8_t subnetPrefixLength = 0;
    int num_write = 0;

    jsonIpAddress = NULL;
    jsonGatewayAddress = NULL;
    jsonSubnetMask = NULL;

    if (NULL == jsonBuffer)
    {
        return r;
    }

    if (0 == json_object_object_get_ex(jsonBuffer, "address", &jsonIpAddress))
    {
        return r;
    }

    strcpy(ipAddr, json_object_get_string(jsonIpAddress));

    if (0 == json_object_object_get_ex(jsonBuffer, "subnet_mask", &jsonSubnetMask))
    {
        return r;
    }

    strcpy(subMask, json_object_get_string(jsonSubnetMask));
#ifndef USE_DEFAULT
/*
OSAL_API int osal_ipv4_netmask_to_prefix(const char *mask, int *prefix)
*/
    (void)osal_ipv4_netmask_to_prefix(subMask, &subnetPrefixLength);

    if (0 == json_object_object_get_ex(jsonBuffer, "gateway", &jsonGatewayAddress))
    {
        return r;
    }

    strcpy(gwAddr, json_object_get_string(jsonGatewayAddress));
    
    char cidr[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    snprintf(&cidr[0], (sizeof(cidr) - 1), "%s/%d", ipAddr, subnetPrefixLength);
    if (OSAL_OK != osal_set_static(OSAL_V4, cidr, gwAddr))
    {
        ERROR_PRINT("Failed to set static IP configuration: %s\n", osal_last_error());
        return r;
    }

    osal_ip_info_t info;
    if (OSAL_OK != osal_get_static_info(OSAL_V4, &info))
    {
        return r;
    }

    snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", info.address);

    ERROR_PRINT("ipAddrV4: %s, strcmpBuf: %s\n", ipAddr, strcmpBuf);
#else
    subnetPrefixLength = calculateSubnetPrefixLength(subMask);

    if (0 == json_object_object_get_ex(jsonBuffer, "gateway", &jsonGatewayAddress))
    {
        return r;
    }

    strcpy(gwAddr, json_object_get_string(jsonGatewayAddress));
    (void)snprintf(&reqBuffer[0], (sizeof(reqBuffer) - 1), "%.100s %.100s %.100s/%d %.100s %.100s",
                   SET_STATIC_IP_SCRIPT,
                   "--ip", ipAddr, subnetPrefixLength,
                   "--gateway", gwAddr);

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return r;
    }

    // validate change from response
    (void)strcpy(reqBuffer, GET_STATIC_IP_SCRIPT);
    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return r;
    }

    num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
    if (num_write >= sizeof(strcmpBuf))
    {
        printf("Value stored in strcmpBuf has been truncated.\n");
    }
#endif

    if (0 == strcmp(ipAddr, strcmpBuf))
    {
        r = 0;
    }

    return r;
}

static int updateConfigEthernetStaticIpv6(struct json_object *jsonBuffer)
{
    int r = -1;
    struct json_object *jsonIpAddress = NULL;
    struct json_object *jsonPrefixLength = NULL;
    struct json_object *jsonGatewayAddress = NULL;

    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strcmpBuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};

    char ipAddr[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char gwAddr[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    uint32_t prefixLength = 0;

    if (NULL == jsonBuffer)
    {
        return r;
    }

    if (0 == json_object_object_get_ex(jsonBuffer,
                                       "address",
                                       &jsonIpAddress))
    {
        return r;
    }

    strcpy(ipAddr, json_object_get_string(jsonIpAddress));

    if (0 == json_object_object_get_ex(jsonBuffer,
                                       "prefix_length",
                                       &jsonPrefixLength))
    {
        return r;
    }

    prefixLength = json_object_get_int(jsonPrefixLength);

    if (0 == json_object_object_get_ex(jsonBuffer,
                                       "gateway",
                                       &jsonGatewayAddress))
    {
        return r;
    }

    strcpy(gwAddr, json_object_get_string(jsonGatewayAddress));
#ifndef USE_DEFAULT
    char cidr[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    snprintf(&cidr[0], (sizeof(cidr) - 1), "%s/%d", ipAddr, prefixLength);
    if (OSAL_OK != osal_set_static(OSAL_V6, cidr, gwAddr))
    {
        ERROR_PRINT("Failed to set static IPv6 configuration: %s\n", osal_last_error());
        return r;
    }

    osal_ip_info_t info;
    if (OSAL_OK != osal_get_static_info(OSAL_V6, &info))
    {
        return r;
    }

    snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", info.address);

    ERROR_PRINT("ipAddrV6: %s, strcmpBuf: %s\n", ipAddr, strcmpBuf);

    if (0 == strcmp(ipAddr, strcmpBuf))
    {
        r = 0;
    }

#else
    (void)snprintf(&reqBuffer[0],
                   (sizeof(reqBuffer) - 1),
                   "%.100s %.100s %.100s/%u %.100s %.100s",
                   SET_STATIC_IPV6_SCRIPT,
                   "--ip",
                   ipAddr,
                   prefixLength,
                   "--gateway",
                   gwAddr);


    if (0 != exec_cmd(reqBuffer,
                      respBuffer,
                      UPDATE_CONFIG_BUFFER_SIZE))
    {
        return r;
    }
    
    r = 0;
#endif

    return r;
}

static int updateConfigNetwork(struct json_object *jsonBuffer)
{
    int r = -1;

    if (0 == json_object_object_length(jsonBuffer))
    {
        return r;
    }

    json_object_object_foreach(jsonBuffer, key, val)
    {
        if (0 == strcmp("ssh", key))
        {
            r = updateConfigNetworkSsh(val);
        }

        if (0 == strcmp("dhcp", key))
        {
            r = updateConfigNetworkDhcp(val);
        }

        if (0 == strcmp("http", key))
        {
            r = updateConfigNetworkHttp(val);
        }

        if (0 == strcmp("dns", key))
        {
            r = updateConfigNetworkDns(val);
        }

        if (0 == strcmp("ntp", key))
        {
            r = updateConfigNetworkNtp(val);
        }
    }

    return r;
}

static int updateConfigNetworkSsh(struct json_object *jsonBuffer)
{
    int r = -1;
    struct json_object *jsonEnabled = NULL;
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strcmpBuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char enabled[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    uint8_t u8_portNum;
    int num_write = 0;

    if (0 == json_object_object_length(jsonBuffer))
    {
        return r;
    }

    if (0 == json_object_object_get_ex(jsonBuffer, "enabled", &jsonEnabled))
    {
        return r;
    }

    (void)strcpy(enabled, json_object_get_string(jsonEnabled));
#ifndef USE_DEFAULT
    int ssh_enabled = !strcmp(enabled, "1") || !strcmp(enabled, "true");

    if (OSAL_OK != osal_set_ssh_enabled(ssh_enabled))
    {
        return r;
    }

    int active = 0;
    if (OSAL_OK != osal_get_ssh_status(&active))
    {
        return r;
    }

    return (ssh_enabled == active) ? 0 : r;
#else
    if ((0 == strcmp("1", enabled)) ||
        (0 == strcmp("true", enabled)))
    {
        (void)strcpy(reqBuffer, SET_SSH_ENABLE_SCRIPT);
        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        (void)strcpy(reqBuffer, GET_SSH_STATUS_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
        if (num_write >= sizeof(strcmpBuf))
        {
            printf("Value stored in strcmpBuf has been truncated.\n");
        }

        if (0 == strcmp("enabled", strcmpBuf))
        {
            r = 0;
        }
    }
    else if ((0 == strcmp("0", enabled)) ||
             (0 == strcmp("false", enabled)))
    {
        (void)strcpy(reqBuffer, SET_SSH_DISABLE_SCRIPT);
        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        (void)strcpy(reqBuffer, GET_SSH_STATUS_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
        if (num_write >= sizeof(strcmpBuf))
        {
            printf("Value stored in strcmpBuf has been truncated.\n");
        }

        if (0 == strcmp("disabled", strcmpBuf))
        {
            r = 0;
        }
    }

    return r;
#endif
}

static int updateConfigNetworkDhcp(struct json_object *jsonBuffer)
{
    int r = -1;
    struct json_object *jsonEnabled = NULL;
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strcmpBuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char enabled[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    uint8_t u8_portNum;
    int num_write = 0;

    if (0 == json_object_object_length(jsonBuffer))
    {
        return r;
    }

    if (0 == json_object_object_get_ex(jsonBuffer, "enabled", &jsonEnabled))
    {
        return r;
    }

    (void)strcpy(enabled, json_object_get_string(jsonEnabled));
#ifndef USE_DEFAULT
/*
 * exclusive=1 keeps the legacy behaviour of enable_dhcp.sh / disable_dhcp.sh:
 *   on  -> DHCP on,  both static families disabled
 *   off -> DHCP off (v4+v6+RA), both static families enabled
 * exclusive=0 only touches the DHCP flags. One transaction, one reload.
OSAL_API int osal_set_dhcp(int enabled, int exclusive);
*/
    int dhcp_enabled = !strcmp(enabled, "1") || !strcmp(enabled, "true");
    if (OSAL_OK != osal_set_dhcp(dhcp_enabled, 1))
    {
        return r;
    }

    int active = 0;
    if (OSAL_OK != osal_get_dhcp_status(&active))
    {
        return r;
    }

    return (dhcp_enabled == active) ? 0 : r;
#else
    if ((0 == strcmp("1", enabled)) ||
        (0 == strcmp("true", enabled)))
    {
        (void)strcpy(reqBuffer, SET_DHCP_ENABLE_SCRIPT);
        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        (void)strcpy(reqBuffer, GET_DHCP_STATUS_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
        if (num_write >= sizeof(strcmpBuf))
        {
            printf("Value stored in strcmpBuf has been truncated.\n");
        }

        if (0 == strcmp("enabled", strcmpBuf))
        {
            r = 0;
        }
    }
    else if ((0 == strcmp("0", enabled)) ||
             (0 == strcmp("false", enabled)))
    {
        (void)strcpy(reqBuffer, SET_DHCP_DISABLE_SCRIPT);
        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        (void)strcpy(reqBuffer, GET_DHCP_STATUS_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
        if (num_write >= sizeof(strcmpBuf))
        {
            printf("Value stored in strcmpBuf has been truncated.\n");
        }

        if (0 == strcmp("disabled", strcmpBuf))
        {
            r = 0;
        }
    }
    return r;
#endif
}

static int updateConfigNetworkHttp(struct json_object *jsonBuffer)
{
    int r = -1;
    struct json_object *jsonEnabled = NULL;
    char strBuffer[MAX_STRING_LENGTH] = {0};
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strcmpBuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char enabled[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    uint8_t u8_portNum;
    int num_write = 0;

    if (0 == json_object_object_length(jsonBuffer))
    {
        return r;
    }

    if (0 == json_object_object_get_ex(jsonBuffer, "enabled", &jsonEnabled))
    {
        return r;
    }

    (void)strcpy(enabled, json_object_get_string(jsonEnabled));

    if ((0 == strcmp("1", enabled)) ||
        (0 == strcmp("true", enabled)))
    {
        (void)strcpy(reqBuffer, SET_HTTP_ENABLE_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        (void)strcpy(reqBuffer, GET_HTTP_STATUS_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
        if (num_write >= sizeof(strcmpBuf))
        {
            printf("Value stored in strcmpBuf has been truncated.\n");
        }

        if (0 == strcmp("enabled", strcmpBuf))
        {
            r = 0;
        }
    }
    else if ((0 == strcmp("0", enabled)) ||
             (0 == strcmp("false", enabled)))
    {
        (void)strcpy(reqBuffer, SET_HTTP_DISABLE_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        (void)strcpy(reqBuffer, GET_HTTP_STATUS_SCRIPT);

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return r;
        }

        num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
        if (num_write >= sizeof(strcmpBuf))
        {
            printf("Value stored in strcmpBuf has been truncated.\n");
        }

        if (0 == strcmp("disabled", strcmpBuf))
        {
            r = 0;
        }
    }

    return r;
}

static int updateConfigNetworkDnsEnabledCheck(struct json_object *jsonBuffer, char *enabled)
{
    struct json_object *jsonEnabled = NULL;
    char strBuffer[MAX_STRING_LENGTH] = {0};
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strcmpBuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    int num_write = 0;

    if (0 == json_object_object_get_ex(jsonBuffer, "enabled", &jsonEnabled))
    {
        return -1;
    }

    (void)strcpy(enabled, json_object_get_string(jsonEnabled));
#ifndef USE_DEFAULT
    int dns_enabled = !strcmp(enabled, "1") || !strcmp(enabled, "true");
    if (OSAL_OK != osal_set_dns_enabled(dns_enabled))
    {
        return -1;
    }

    int active = 0;
    if (OSAL_OK != osal_get_dns_status(&active))
    {
        return -1;
    }

    if (dns_enabled == active)
    {
        return 0;
    }
#else
    if ((0 == strcmp("1", enabled)) ||
        (0 == strcmp("true", enabled)))
    {
        (void)strcpy(reqBuffer, SET_DNS_ENABLE_SCRIPT);
        (void)strcpy(strBuffer, "enabled");
    }
    else if ((0 == strcmp("0", enabled)) ||
             (0 == strcmp("false", enabled)))
    {
        (void)strcpy(reqBuffer, SET_DNS_DISABLE_SCRIPT);
        (void)strcpy(strBuffer, "disabled");
    }

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    (void)strcpy(reqBuffer, GET_DNS_STATUS_SCRIPT);

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
    if (num_write >= sizeof(strcmpBuf))
    {
        printf("Value stored in strcmpBuf has been truncated.\n");
    }

    if (0 == strcmp(strBuffer, strcmpBuf))
    {
        return 0;
    }

    return -1;
#endif
}

static int updateConfigNetworkDnsConfigCheck(struct json_object *jsonBuffer, char *enabled)
{
    struct json_object *jsonConfig = NULL;
    struct json_object *jsonServerNames = NULL;
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strbuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char svrList[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char svrListDb[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    int num_write = 0;

    if (0 == json_object_object_get_ex(jsonBuffer, "config", &jsonConfig) && ((0 == strcmp("1", enabled)) || (0 == strcmp("true", enabled))))
    {
        return -1;
    }

    if (0 == json_object_object_get_ex(jsonConfig, "server_names", &jsonServerNames))
    {
        return -1;
    }

    if (0 == json_object_array_length(jsonServerNames))
    {
#ifndef USE_DEFAULT
        ERROR_PRINT("SUPER EMPTYING DNS\n");
        if (OSAL_OK != osal_set_dns(NULL, 0))
        {
            ERROR_PRINT("Failed to clear NTP server names: %s\n", osal_last_error());
            return -1;
        }
#else
    {
        (void)snprintf(&reqBuffer[0], sizeof(reqBuffer), "%.100s %.100s %.100s",
                    SET_DNS_REPLACE_SERVER_SCRIPT,
                    "--servers", " ");

        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return -1;
        }
#endif
        return 0;
    }
    
    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", json_object_get_string(jsonServerNames));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in jsonServerNames has been truncated.\n");
    }

#ifndef USE_DEFAULT

    char pool[OSAL_MAX_SERVERS][OSAL_HOST_LEN];
    const char *servers[OSAL_MAX_SERVERS];
    int count = 0;

    if (OSAL_OK != osal_parse_server_list(strbuf, pool, servers, &count))
    {
        ERROR_PRINT("Failed to parse DNS server names: %s\n", osal_last_error());
        return -1;
    }

    if(OSAL_OK != osal_set_dns(servers, count))
    {
        ERROR_PRINT("Failed to set DNS server names: %s\n", osal_last_error());
        return -1;
    }
    
#else
    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", removeCharacter(&strbuf[0], '['));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in jsonServerNames has been truncated.\n");
    }

    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", removeCharacter(&strbuf[0], ']'));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in jsonServerNames has been truncated.\n");
    }

    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", removeCharacter(&strbuf[0], '\"'));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in jsonServerNames has been truncated.\n");
    }

    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", trimLeadAndTrailCharacter(&strbuf[0], ' '));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in jsonServerNames has been truncated.\n");
    }

    memset(&reqBuffer[0], 0, UPDATE_CONFIG_BUFFER_SIZE);
    (void)snprintf(&reqBuffer[0], sizeof(reqBuffer), "%.100s %.100s %.100s",
                SET_DNS_REPLACE_SERVER_SCRIPT,
                "--servers", strbuf);

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    // Write validation
    (void)strcpy(reqBuffer, READ_DNS_SERVER_SCRIPT);

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    num_write = snprintf(&svrListDb[0], sizeof(svrListDb), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
    if (num_write >= sizeof(svrListDb))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    num_write = snprintf(&svrListDb[0], sizeof(svrListDb), "%s", removeCharacter(&svrListDb[0], '['));
    if (num_write >= sizeof(svrListDb))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    num_write = snprintf(&svrListDb[0], sizeof(svrListDb), "%s", removeCharacter(&svrListDb[0], ']'));
    if (num_write >= sizeof(svrListDb))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    if (0 == strcmp(svrList, svrListDb))
    {
        return 0;
    }
    else
    {
        return -1;
    }
#endif
}

static int updateConfigNetworkDns(struct json_object *jsonBuffer)
{
    int r = -1;
    char enabled[UPDATE_CONFIG_BUFFER_SIZE] = {0};

    if (0 == json_object_object_length(jsonBuffer))
    {
        return r;
    }

    // no checking needed since any skip inside the function will
    // lead to invoking the next function (based on original code)
    updateConfigNetworkDnsEnabledCheck(jsonBuffer, enabled);

    r = updateConfigNetworkDnsConfigCheck(jsonBuffer, enabled);

    return r;
}

static int updateConfigNetworkNtpEnabledCheck(struct json_object *jsonBuffer, char *enabled)
{
    struct json_object *jsonEnabled = NULL;
    char strBuffer[MAX_STRING_LENGTH] = {0};
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strcmpBuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    int num_write = 0;

    if (0 == json_object_object_length(jsonBuffer))
    {
        return -1;
    }

    if (0 == json_object_object_get_ex(jsonBuffer, "enabled", &jsonEnabled))
    {
        return -1;
    }

    (void)strcpy(enabled, json_object_get_string(jsonEnabled));
#ifndef USE_DEFAULT
    int ntp_enabled = !strcmp(enabled, "1") || !strcmp(enabled, "true");
    if (OSAL_OK != osal_set_ntp_enabled(ntp_enabled))
    {
        return -1;
    }

    int active = 0;
    if (OSAL_OK != osal_get_ntp_status(&active))
    {
        return -1;
    }

    if (ntp_enabled == active)
    {
        return 0;
    }
#else
    if ((0 == strcmp("1", enabled)) ||
        (0 == strcmp("true", enabled)))
    {
        (void)strcpy(reqBuffer, SET_NTP_ENABLE_SCRIPT);
        (void)strcpy(strBuffer, "enabled");
    }
    else if ((0 == strcmp("0", enabled)) ||
             (0 == strcmp("false", enabled)))
    {
        (void)strcpy(reqBuffer, SET_NTP_DISABLE_SCRIPT);
        (void)strcpy(strBuffer, "disabled");
    }

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    (void)strcpy(reqBuffer, GET_NTP_STATUS_SCRIPT);

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    num_write = snprintf(&strcmpBuf[0], sizeof(strcmpBuf), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
    if (num_write >= sizeof(strcmpBuf))
    {
        printf("Value stored in strcmpBuf has been truncated.\n");
    }

    if (0 == strcmp(strBuffer, strcmpBuf))
    {
        return 0;
    }
#endif
    return -1;
}

static int updateConfigNetworkNtpConfigCheck(struct json_object *jsonBuffer, char *enabled)
{
    struct json_object *jsonConfig = NULL;
    struct json_object *jsonServerNames = NULL;
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strbuf[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char svrList[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char svrListDb[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    int num_write = 0;

    if (0 == json_object_object_get_ex(jsonBuffer, "config", &jsonConfig) && ((0 == strcmp("1", enabled)) || (0 == strcmp("true", enabled))))
    {
        return -1;
    }

    if (0 == json_object_object_get_ex(jsonConfig, "server_names", &jsonServerNames))
    {
        return -1;
    }

    if (0 == json_object_array_length(jsonServerNames))
    {
#ifndef USE_DEFAULT
        ERROR_PRINT("SUPER EMPTYING NTP\n");
        if (OSAL_OK != osal_set_ntp(NULL, 0))
        {
            ERROR_PRINT("Failed to clear NTP server names: %s\n", osal_last_error());
            return -1;
        }
#else
        (void)snprintf(&reqBuffer[0], sizeof(reqBuffer), "%.100s %.100s %.100s",
                    SET_NTP_REPLACE_SERVER_SCRIPT,
                    "--servers", " ");
        ERROR_PRINT("Command for NTP: %s\n",reqBuffer);
        if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
        {
            return -1;
        }
#endif

        return 0;
    }

    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", json_object_get_string(jsonServerNames));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

#ifndef USE_DEFAULT

    char pool[OSAL_MAX_SERVERS][OSAL_HOST_LEN];
    const char *servers[OSAL_MAX_SERVERS];
    int count = 0;

    if (OSAL_OK != osal_parse_server_list(strbuf, pool, servers, &count))
    {
        ERROR_PRINT("Failed to parse NTP server names: %s\n", osal_last_error());
        return -1;
    }

    if(OSAL_OK != osal_set_ntp(servers, count))
    {
        ERROR_PRINT("Failed to set NTP server names: %s\n", osal_last_error());
        return -1;
    }
    
#else
    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", removeCharacter(&strbuf[0], '['));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", removeCharacter(&strbuf[0], ']'));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", removeCharacter(&strbuf[0], '\"'));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    num_write = snprintf(&svrList[0], sizeof(strbuf), "%s", trimLeadAndTrailCharacter(&strbuf[0], ' '));
    if (num_write >= sizeof(strbuf))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    memset(&reqBuffer[0], 0, UPDATE_CONFIG_BUFFER_SIZE);
    (void)snprintf(&reqBuffer[0], sizeof(reqBuffer) - 1, "%.100s %.100s %.100s",
                   SET_NTP_REPLACE_SERVER_SCRIPT,
                   "--servers", svrList);

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    // Write validation
    (void)strcpy(reqBuffer, READ_NTP_SERVER_SCRIPT);

    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return -1;
    }

    num_write = snprintf(&svrListDb[0], sizeof(svrListDb), "%s", trimTrailingCharacter(&respBuffer[0], '\n'));
    if (num_write >= sizeof(svrListDb))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    num_write = snprintf(&svrListDb[0], sizeof(svrListDb), "%s", removeCharacter(&svrListDb[0], '['));
    if (num_write >= sizeof(svrListDb))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    num_write = snprintf(&svrListDb[0], sizeof(svrListDb), "%s", removeCharacter(&svrListDb[0], ']'));
    if (num_write >= sizeof(svrListDb))
    {
        printf("Value stored in buffer has been truncated.\n");
    }

    if (0 == strcmp(svrList, svrListDb))
    {
        return 0;
    }
    else
    {
        return -1;
    }
#endif
}

static int updateConfigNetworkNtp(struct json_object *jsonBuffer)
{
    int r = -1;
    char enabled[UPDATE_CONFIG_BUFFER_SIZE] = {0};

    // no checking needed since any skip inside the function will
    // lead to invoking the next function (based on original code)
    updateConfigNetworkNtpEnabledCheck(jsonBuffer, enabled);

    r = updateConfigNetworkNtpConfigCheck(jsonBuffer, enabled);

    return r;
}

int cb_process_config_request(char *p_data)
{
    struct json_object *jsonObject = NULL;
    struct json_object *jsonTimestamp = NULL;
    struct json_object *jsonManagerId = NULL;
    struct json_object *systemTime = NULL;
    struct json_object *jsonTypeData = NULL;
    struct json_object *jsonNetworkData = NULL;
    struct json_object *jsonPhysicalCommsData = NULL;
    struct json_object *jsonConfigData = NULL;

    jsonObject = json_tokener_parse(p_data);

    (void)json_object_object_get_ex(jsonObject, "timestamp", &jsonTimestamp);
    if (false == json_object_object_get_ex(jsonObject, "manager_id", &jsonManagerId))
    {
        return -1;
    }

    if (true != json_object_object_get_ex(jsonObject, "config", &jsonConfigData))
    {
        json_object_put(jsonObject);

        return 0;
    }

 // SYSTEM TIME
    if (true == json_object_object_get_ex(jsonConfigData, "system_time", &systemTime))
    {
        proccessSystemTime((uint8_t)json_object_get_int(jsonManagerId),systemTime);
    }
    else{
        ERROR_PRINT("no systemTime\n");
    }


    // NETWORK
    if (true == json_object_object_get_ex(jsonConfigData, "network", &jsonNetworkData) &&
        true == json_object_object_get_ex(jsonNetworkData, "type", &jsonTypeData))
    {
        (void)processConfigNetwork((uint8_t)json_object_get_int(jsonManagerId), jsonTypeData);
    }

    // PHYSICAL COMMS
    if (true == json_object_object_get_ex(jsonConfigData, "physical_comms", &jsonPhysicalCommsData) &&
        true == json_object_object_get_ex(jsonPhysicalCommsData, "type", &jsonTypeData))
    {
        (void)processConfigPhysicalComms((uint8_t)json_object_get_int(jsonManagerId), jsonTypeData);
    }

    json_object_put(jsonObject);

#ifdef ENABLE_TEST_HOOKS
    __lsan_do_leak_check();
#endif 

    return 0;
}

/* Private Definitions */
static int proccessSystemTime(uint8_t u8_managerId, struct json_object *jsonTImeObj)
{
    ERROR_PRINT("ntp is disabled\n");

    int r = -1;
    struct json_object *jsonRootBuffer = NULL;

    jsonRootBuffer = json_object_new_object();  

    (void)json_object_object_add(jsonRootBuffer,
                            "manager_id",
                            json_object_new_int(u8_managerId));

    json_object_object_add(jsonRootBuffer,
                        "system_time",
                        json_object_get(jsonTImeObj));

    r = pushConfigBuffer(jsonRootBuffer);

    json_object_put(jsonRootBuffer);
    
    return r;

}

static int processConfigPhysicalComms(uint8_t u8_managerId, struct json_object *jsonObj)
{
    int r = -1;
    int jsonArrayCount = 0;
    struct json_object *jsonEthernetBuffer = NULL;
    struct json_object *jsonSerialBuffer = NULL;
    struct json_object *jsonRawData = NULL;

    if (NULL == jsonObj)
    {
        return r;
    }

    if (0 == json_object_object_get_ex(jsonObj, "ethernet", &jsonEthernetBuffer))
    {
        return r;
    }

    if (is_config_physical_comms_writeable(DBASE_MANAGER_FILE, "config_physical_comms", u8_managerId, "ETHERNET"))
    {
        jsonArrayCount = (int)json_object_array_length(jsonEthernetBuffer);

        for (uint8_t i = 0; i < jsonArrayCount; i++)
        {
            jsonRawData = json_object_array_get_idx(jsonEthernetBuffer, i);
            (void)processConfigEthernet(jsonRawData, u8_managerId);
        }
    }

    return 0;
}

static int processConfigEthernet(struct json_object *jsonObj, uint8_t u8_managerId)
{
    int r = -1;
    int jsonArrayCount = 0;
    struct json_object *jsonHostname;
    struct json_object *jsonStaticIp;
    struct json_object *jsonIpv6Static;
    struct json_object *jsonConfigBuffer;
    struct json_object *jsonRootBuffer;
    struct json_object *jsonBuffer;
    char portname[UPDATE_CONFIG_BUFFER_SIZE] = {0};

    if (NULL == jsonObj)
    {
        return r;
    }

    // get config
    (void)json_object_object_get_ex(jsonObj, "config", &jsonConfigBuffer);

    json_object_object_foreach(jsonObj, key, val)
    {
        if (0 != strcmp("port_name", key))
        {
            continue;
        }

        strcpy(portname, json_object_get_string(val));
        if (0 == json_object_object_length(jsonConfigBuffer))
        {
            continue;
        }

        if (0 != json_object_object_get_ex(jsonConfigBuffer, "hostname", &jsonHostname))
        {
            jsonRootBuffer = json_object_new_object();
            jsonBuffer = json_object_new_object();

            (void)json_object_object_add(jsonBuffer,
                                         "port_name",
                                         json_object_new_string(portname));
            (void)json_object_object_add(jsonBuffer,
                                         "hostname",
                                         json_object_get(jsonHostname));

            (void)json_object_object_add(jsonRootBuffer,
                                         "manager_id",
                                         json_object_new_int(u8_managerId));

            (void)json_object_object_add(jsonRootBuffer, "ethernet", jsonBuffer);

            r = pushConfigBuffer(jsonRootBuffer);
            json_object_put(jsonRootBuffer);
        }

        if (0 != json_object_object_get_ex(jsonConfigBuffer, "ipv4_static", &jsonStaticIp))
        {
            jsonRootBuffer = json_object_new_object();
            jsonBuffer = json_object_new_object();

            (void)json_object_object_add(jsonBuffer,
                                         "port_name",
                                         json_object_new_string(portname));
            (void)json_object_object_add(jsonBuffer,
                                         "ipv4_static",
                                         json_object_get(jsonStaticIp));

            (void)json_object_object_add(jsonRootBuffer,
                                         "manager_id",
                                         json_object_new_int(u8_managerId));

            (void)json_object_object_add(jsonRootBuffer, "ethernet", jsonBuffer);

            r = pushConfigBuffer(jsonRootBuffer);
            json_object_put(jsonRootBuffer);
        }

        /* IPv6 Static Configuration */
        if (0 != json_object_object_get_ex(jsonConfigBuffer, "ipv6_static", &jsonIpv6Static))
        {
            jsonRootBuffer = json_object_new_object();
            jsonBuffer = json_object_new_object();

            (void)json_object_object_add(jsonBuffer,
                                         "port_name",
                                         json_object_new_string(portname));
            (void)json_object_object_add(jsonBuffer,
                                         "ipv6_static",
                                         json_object_get(jsonIpv6Static));

            (void)json_object_object_add(jsonRootBuffer,
                                         "manager_id",
                                         json_object_new_int(u8_managerId));

            (void)json_object_object_add(jsonRootBuffer,
                                         "ethernet",
                                         jsonBuffer);
 
            r = pushConfigBuffer(jsonRootBuffer);
            json_object_put(jsonRootBuffer);
         }
    }

    return r;
}

static int processConfigNetwork(uint8_t u8_managerId, struct json_object *jsonObj)
{
    int r = -1;
    int jsonArrayCount = 0;
    struct json_object *jsonEnabledData = NULL;
    struct json_object *jsonEnabledBuffer = NULL;

    bool sshEnable = false;
    bool dhcpEnable = false;
    bool httpEnable = false;
    bool dnsEnable = false;
    bool ntpEnable = false;
    bool ssdpEnable = false;

    if (true != json_object_object_get_ex(jsonObj, "enabled", &jsonEnabledBuffer))
    {
        return r;
    }

    jsonArrayCount = (int)json_object_array_length(jsonEnabledBuffer);
    for (uint8_t i = 0; i < jsonArrayCount; i++)
    {
        jsonEnabledData = json_object_array_get_idx(jsonEnabledBuffer, i);

        if (0 == strcmp("ssh", json_object_get_string(jsonEnabledData)))
        {
            sshEnable = true;
        }
        else if (0 == strcmp("dhcp", json_object_get_string(jsonEnabledData)))
        {
            dhcpEnable = true;
        }
        else if (0 == strcmp("http", json_object_get_string(jsonEnabledData)))
        {
            httpEnable = true;
        }
        else if (0 == strcmp("dns", json_object_get_string(jsonEnabledData)))
        {
            dnsEnable = true;
        }
        else if (0 == strcmp("ntp", json_object_get_string(jsonEnabledData)))
        {
            ntpEnable = true;
        }
        else if (0 == strcmp("ssdp", json_object_get_string(jsonEnabledData)))
        {
            ssdpEnable = true;
        }
    }

    json_object_object_foreach(jsonObj, key, val)
    {
        if (0 == strcmp("ssh", key) && is_config_writeable(DBASE_MANAGER_FILE, "config_network", u8_managerId, key))
        {
            r = processConfigNetworkSsh(val, u8_managerId, sshEnable);
        }
        else if (0 == strcmp("dhcp", key) && is_config_writeable(DBASE_MANAGER_FILE, "config_network", u8_managerId, key))
        {
            r = processConfigNetworkDhcp(val, u8_managerId, dhcpEnable);
        }
        else if (0 == strcmp("http", key) && is_config_writeable(DBASE_MANAGER_FILE, "config_network", u8_managerId, key))
        {
            r = processConfigNetworkHttp(val, u8_managerId, httpEnable);
        }
        else if (0 == strcmp("dns", key) && is_config_writeable(DBASE_MANAGER_FILE, "config_network", u8_managerId, key))
        {
            r = processConfigNetworkDns(val, u8_managerId, dnsEnable);
        }        
        else if (0 == strcmp("ntp", key) && is_config_writeable(DBASE_MANAGER_FILE, "config_network", u8_managerId, key))
        {
            r = processConfigNetworkNtp(val, u8_managerId, ntpEnable);
        }
        else if (0 == strcmp("ssdp", key) && is_config_writeable(DBASE_MANAGER_FILE, "config_network", u8_managerId, key))
        {
            r = processConfigNetworkSsdp(val, u8_managerId, ssdpEnable);
        }
        else
        {
            /* do nothing */
        }

 }

    return r;
}

static int updateSystemTime(struct json_object *jsonTimeObj)
{
    int r = -1;
     struct tm tm_parsed = {0};
    
    char systemTime[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char strBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char reqBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    char respBuffer[UPDATE_CONFIG_BUFFER_SIZE] = {0};
    int num_write = 0;

    snprintf(&systemTime[0], sizeof(systemTime), "%s",  json_object_get_string(jsonTimeObj));

    strptime(systemTime, "%Y-%m-%dT%H:%M:%S", &tm_parsed);
    strftime(strBuffer, sizeof(strBuffer), "%Y-%m-%d %H:%M", &tm_parsed);
    puts(strBuffer);

    (void)snprintf(&reqBuffer[0], sizeof(reqBuffer), "%.100s %.100s \"%.100s\" %.100s",
                SET_SYSTEM_TIME,
                "--utc", strBuffer,"-y");
    
    if (0 != exec_cmd(reqBuffer, respBuffer, UPDATE_CONFIG_BUFFER_SIZE))
    {
        return r;
    }

    return 0;
}

static int processConfigNetworkSsh(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled)
{
    int r = -1;
    struct json_object *jsonRootBuffer = NULL;
    struct json_object *jsonConfigBuffer = NULL;
    struct json_object *jsonBuffer = NULL;

    jsonBuffer = json_object_new_object();
    jsonRootBuffer = json_object_new_object();

    jsonConfigBuffer = jsonObj;
    (void)json_object_object_add(jsonConfigBuffer,
                                 "enabled",
                                 json_object_new_boolean(b_enabled));

    (void)json_object_object_add(jsonBuffer,
                                 "ssh",
                                 json_object_get(jsonConfigBuffer));

    (void)json_object_object_add(jsonRootBuffer,
                                 "manager_id",
                                 json_object_new_int(u8_managerId));

    (void)json_object_object_add(jsonRootBuffer,
                                 "network",
                                 jsonBuffer);

    r = pushConfigBuffer(jsonRootBuffer);
    json_object_put(jsonRootBuffer);

    return r;
}

static int processConfigNetworkDhcp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled)
{
    int r = -1;
    struct json_object *jsonRootBuffer = NULL;
    struct json_object *jsonConfigBuffer = NULL;
    struct json_object *jsonBuffer = NULL;

    jsonBuffer = json_object_new_object();
    jsonRootBuffer = json_object_new_object();

    jsonConfigBuffer = jsonObj;
    (void)json_object_object_add(jsonConfigBuffer,
                                 "enabled",
                                 json_object_new_boolean(b_enabled));

    (void)json_object_object_add(jsonBuffer,
                                 "dhcp",
                                 json_object_get(jsonConfigBuffer));

    (void)json_object_object_add(jsonRootBuffer,
                                 "manager_id",
                                 json_object_new_int(u8_managerId));

    (void)json_object_object_add(jsonRootBuffer,
                                 "network",
                                 jsonBuffer);

    r = pushConfigBuffer(jsonRootBuffer);
    json_object_put(jsonRootBuffer);

    return r;
}

static int processConfigNetworkHttp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled)
{
    int r = -1;
    struct json_object *jsonRootBuffer;
    struct json_object *jsonConfigBuffer;
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();
    jsonRootBuffer = json_object_new_object();

    jsonConfigBuffer = jsonObj;
    (void)json_object_object_add(jsonConfigBuffer,
                                 "enabled",
                                 json_object_new_boolean(b_enabled));

    (void)json_object_object_add(jsonBuffer,
                                 "http",
                                 json_object_get(jsonConfigBuffer));

    (void)json_object_object_add(jsonRootBuffer,
                                 "manager_id",
                                 json_object_new_int(u8_managerId));

    (void)json_object_object_add(jsonRootBuffer,
                                 "network",
                                 jsonBuffer);

    r = pushConfigBuffer(jsonRootBuffer);
    json_object_put(jsonRootBuffer);

    return r;
}

static int processConfigNetworkDns(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled)
{
    int r = -1;
    struct json_object *jsonRootBuffer;
    struct json_object *jsonConfigBuffer;
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();
    jsonRootBuffer = json_object_new_object();

    jsonConfigBuffer = jsonObj;
    (void)json_object_object_add(jsonConfigBuffer,
                                 "enabled",
                                 json_object_new_boolean(b_enabled));

    (void)json_object_object_add(jsonBuffer,
                                 "dns",
                                 json_object_get(jsonConfigBuffer));

    (void)json_object_object_add(jsonRootBuffer,
                                 "manager_id",
                                 json_object_new_int(u8_managerId));

    (void)json_object_object_add(jsonRootBuffer,
                                 "network",
                                 jsonBuffer);

    r = pushConfigBuffer(jsonRootBuffer);
    json_object_put(jsonRootBuffer);

    return r;
}

static int processConfigNetworkNtp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled)
{
    int r = -1;
    struct json_object *jsonRootBuffer;
    struct json_object *jsonConfigBuffer;
    struct json_object *jsonBuffer;

    jsonBuffer = json_object_new_object();
    jsonRootBuffer = json_object_new_object();
    jsonConfigBuffer = jsonObj;

    (void)json_object_object_add(jsonConfigBuffer,
                                 "enabled",
                                 json_object_new_boolean(b_enabled));

    (void)json_object_object_add(jsonBuffer,
                                 "ntp",
                                 json_object_get(jsonConfigBuffer));

    (void)json_object_object_add(jsonRootBuffer,
                                 "manager_id",
                                 json_object_new_int(u8_managerId));

    (void)json_object_object_add(jsonRootBuffer,
                                 "network",
                                 jsonBuffer);

    r = pushConfigBuffer(jsonRootBuffer);
    json_object_put(jsonRootBuffer);

    return r;
}

static int processConfigNetworkSsdp(struct json_object *jsonObj, uint8_t u8_managerId, bool b_enabled)
{
    int r = -1;
    struct json_object *jsonRootBuffer = NULL;
    struct json_object *jsonConfigBuffer = NULL;
    struct json_object *jsonBuffer = NULL;

    jsonBuffer = json_object_new_object();
    jsonRootBuffer = json_object_new_object();
    jsonConfigBuffer = jsonObj;

    (void)json_object_object_add(jsonConfigBuffer,
                                 "enabled",
                                 json_object_new_boolean(b_enabled));

    (void)json_object_object_add(jsonBuffer,
                                 "ssdp",
                                 json_object_get(jsonConfigBuffer));

    (void)json_object_object_add(jsonRootBuffer,
                                 "manager_id",
                                 json_object_new_int(u8_managerId));

    (void)json_object_object_add(jsonRootBuffer,
                                 "network",
                                 jsonBuffer);

    r = pushConfigBuffer(jsonRootBuffer);
    json_object_put(jsonRootBuffer);

    return r;
}

static int pushConfigBuffer(struct json_object *jsonBuffer)
{
    char strBuffer[MAX_STRING_LENGTH] = {0};
    int num_write = 0;

    if (NULL != jsonBuffer)
    {
        // TODO: insert manager_id here

        num_write = snprintf(&strBuffer[0], sizeof(strBuffer), "%s",
                             json_object_to_json_string_ext(jsonBuffer,
                                                            (JSON_C_TO_STRING_SPACED | JSON_C_TO_STRING_PRETTY | JSON_C_TO_STRING_NOSLASHESCAPE)));
        if (num_write >= sizeof(strBuffer))
        {
            printf("Value stored in strBuffer has been truncated.\n");
        }

        (void)enqueue(strBuffer);
    }

    return 0;
}

static int callPollResource(void)
{
    int r = -1;
    char buffer[CALL_POLL_RESOURCE_BUFFER_SIZE] = {0};
    char strbuf[CALL_POLL_RESOURCE_BUFFER_SIZE] = {0};
    int num_write = 0;

    if (0 == exec_cmd(CALL_POLL_RESOURCE, &buffer[0], ARRAY_SIZE(buffer)))
    {
        num_write = snprintf(&strbuf[0], sizeof(strbuf), "%s", trimTrailingCharacter(&buffer[0], '\n'));
        if (num_write >= sizeof(strbuf))
        {
            printf("Value stored in buffer has been truncated.\n");
        }

        if (0 == strcmp("0", strbuf))
        {
            r = 0;
        }
        else
        {
            /* Do nothing */
        }
    }

    return r;
}

static shelf_type_et get_shelf_type(uint8_t u8_managerId)
{
    shelf_type_et e_type = SHELF_TYPE_UNKNOWN;

    sqlite3 *fd_db;
    sqlite3_stmt *stmt;
    char sql_cmd[MAX_STRING_LENGTH] = {0};
    char buf[MAX_STRING_LENGTH] = {0};
    size_t sz_len = 0;
    int num_write = 0;

    if (0 == sqlite3_open(MANAGER_DB_FILE, &fd_db))
    {
        num_write = snprintf(&sql_cmd[0], sizeof(sql_cmd),
                             "SELECT master_shelf_type FROM list WHERE manager_id=%d LIMIT 1;",
                             u8_managerId);

        if (num_write >= sizeof(sql_cmd))
        {
            printf("Value stored in buffer has been truncated.\n");
        }

        if (SQLITE_OK == sqlite3_prepare_v2(fd_db, sql_cmd, -1, &stmt, NULL) && sqlite3_step(stmt) == SQLITE_ROW)
        {
            sz_len = sqlite3_column_bytes(stmt, 0);
            memcpy(&buf[0], sqlite3_column_text(stmt, 0), sz_len);
        }

        (void)sqlite3_finalize(stmt);
    }

    sqlite3_close(fd_db);

    if (strstr(buf, "PSU_SHELF"))
    {
        e_type = SHELF_TYPE_PSU;
    }
    else if (strstr(buf, "BBU_SHELF"))
    {
        e_type = SHELF_TYPE_BBU;
    }

    return e_type;
}

static bool is_config_writeable(const char *dbFile, char *table, uint8_t u8_managerId, char *protocol)
{
    size_t sz_len = 0;
    sqlite3 *fd_db;
    sqlite3_stmt *stmt;
    char sql_cmd[MAX_STRING_LENGTH] = {0};
    char buf[MAX_STRING_LENGTH] = {0};
    bool isWriteable = false;
    int num_write = 0;

    if (0 == sqlite3_open(dbFile, &fd_db))
    {
        /* set to true if atleast one is true */
        num_write = snprintf(&sql_cmd[0], sizeof(sql_cmd),
                             "SELECT COUNT(writeable) FROM %s WHERE manager_id=%d AND protocol=\"%s\" AND writeable = \"true\";",
                             table, u8_managerId, protocol);

        if (num_write >= sizeof(sql_cmd))
        {
            printf("Value stored in buffer has been truncated.\n");
        }

        if (SQLITE_OK == sqlite3_prepare_v2(fd_db, sql_cmd, -1, &stmt, NULL) &&
            sqlite3_step(stmt) == SQLITE_ROW)
        {
            sz_len = sqlite3_column_int(stmt, 0);
        }

        (void)sqlite3_finalize(stmt);
    }

    sqlite3_close(fd_db);

    if (0 < sz_len)
    {
        isWriteable = true;
    }

    return isWriteable;
}

static bool is_config_physical_comms_writeable(const char *dbFile, char *table, uint8_t u8_managerId, char *type)
{
    size_t sz_len = 0;
    sqlite3 *fd_db;
    sqlite3_stmt *stmt;
    char sql_cmd[MAX_STRING_LENGTH] = {0};
    char buf[MAX_STRING_LENGTH] = {0};
    bool isWriteable = false;
    int num_write = 0;

    if (0 == sqlite3_open(dbFile, &fd_db))
    {
        /* set to true if atleast one is true */
        num_write = snprintf(&sql_cmd[0], sizeof(sql_cmd),
                             "SELECT COUNT(writeable) FROM %s WHERE manager_id=%d AND type=\"%s\" AND writeable = \"true\";",
                             table, u8_managerId, type);

        if (num_write >= sizeof(sql_cmd))
        {
            printf("Value stored in buffer has been truncated.\n");
        }

        if (SQLITE_OK == sqlite3_prepare_v2(fd_db, sql_cmd, -1, &stmt, NULL) &&
            sqlite3_step(stmt) == SQLITE_ROW)
        {
            sz_len = sqlite3_column_int(stmt, 0);
        }

        (void)sqlite3_finalize(stmt);
    }

    sqlite3_close(fd_db);

    if (0 < sz_len)
    {
        isWriteable = true;
    }

    return isWriteable;
}
