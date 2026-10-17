#define _XOPEN_SOURCE 700

#include "monitor-config.h"
#include "data-schema.h"
#include "helpers.h"

#include "osal_network.h"

#include <string.h>
#include <stdlib.h>
#include <stdio.h>

#include <time.h>

/** private constants */
static const char GET_SHELF_AMBIENT_TEMP_WARN_THRESHOLD[] =
    "busctl get-property ewd.shelf.adctempmodule1  /ewd/shelf/adctempmodule1/temp0 ewd.shelf.adctempmodule1.warningInterface WarningHighAssert  | awk '{ print $2; }'";
static const char GET_SSH_STATUS_SCRIPT[] =
    "get_ssh_status.sh";

static const char GET_HTTP_STATUS_SCRIPT[] =
    "get_http_status.sh";
#ifdef USE_DEFAULT
static const char GET_DNS_SERVER_LIST_CMD[] =
    "cat /etc/systemd/network/80-wired.network.d/80-wired.conf | grep DNS | cut -d= -f2 | sed -n '1 p'";

static const char GET_DNS_STATUS_SCRIPT[] =
    "get_dns_status.sh";

static const char GET_NTP_SERVER_LIST_CMD[] =
    "cat /etc/systemd/timesyncd.conf | grep NTP | cut -d= -f2 | sed -n '1 p'";

static const char GET_NTP_STATUS_SCRIPT[] =
    "get_ntp_status.sh";

static const char GET_TIMEZONE_CMD[] =
    "timedatectl | grep \"Time zone\" | cut -d: -f2";

static const char GET_ACTIVE_FW_VERSION_CMD[] =
    "cat /etc/os-release | grep \"VERSION=\" | cut -d= -f2";

static const char GET_HOSTNAME_SCRIPT[] =
    "get_hostname.sh | awk '{print $2}'";

static const char GET_ETHERNET_MAC_ADDRESS_CMD[] =
    "cat /sys/class/net/eth0/address";

static const char GET_ETHERNET_SPEED_CMD[] =
    "cat /sys/class/net/eth0/speed";

static const char GET_ETHERNET_MTU_SIZE_CMD[] =
    "cat /sys/class/net/eth0/mtu";

static const char GET_ETHERNET_STATUS_CMD[] =
    "cat /sys/class/net/eth0/operstate";

static const char GET_ETHERNET_DUPLEX_CMD[] =
    "cat /sys/class/net/eth0/duplex";

static const char GET_ETHERNET_NEGOTIATE_CMD[] =
    "ethtool eth0 | grep \"Auto-negotiation:\" | awk '{ print $2; }'";

static const char GET_PMC_DHCP_STATUS[] =
    "ip -o -f inet addr | awk '/scope global/ {print}' | awk '/eth0/ { print }'";

static const char GET_ETHERNET_STATIC_IP_ADDRESS_CMD[] =
    "get_static_ip.sh | grep 'ip' | awk '{print $2}' | cut -d'/' -f1 | cut -d',' -f1";

static const char GET_ETHERNET_DYNAMIC_IP_ADDRESS_CMD[] =
    "get_dynamic_ip.sh | grep 'ip' | awk '{print $2}' | cut -d'/' -f1 | cut -d',' -f1";

static const char GET_ETHERNET_STATIC_GATEWAY_CMD[] =
    "get_static_ip.sh | grep 'gateway' | awk '{print $2}' | cut -d'/' -f1 | cut -d',' -f1";

static const char GET_ETHERNET_DHCP_STATUS_CMD[] = 
    "get_dhcp_status.sh | grep 'status: '| awk '{print $2}'";

static const char GET_ETHERNET_DYNAMIC_GATEWAY_CMD[] =
    "get_dynamic_ip.sh | grep 'gateway' | awk '{print $2}' | cut -d'/' -f1 | cut -d',' -f1";

static const char GET_ETHERNET_DYNAMIC_SUBNET_MASK_CMD[] =
    "get_dynamic_ip.sh | grep 'ip' | awk '{print $2}' | cut -d'/' -f2 | cut -d',' -f1";

static const char GET_ETHERNET_STATIC_SUBNET_MASK_CMD[] =
    "get_static_ip.sh | grep 'ip' | awk '{print $2}' | cut -d'/' -f2 | cut -d',' -f1";
#endif
static const char GET_SERIAL_DEBUG_PORT_DETAILS_CMD[] =
    "dmesg | grep -i \"Kernel command line:\" | awk '{ print $6; }'";

static const char GET_HTTPS_CERT_INFO[] =
    "read_https_cert_info.sh";

static const char GET_DYNAMIC_IPV6_PREFIX_LENGTH[] =
    "get_dynamic_ipv6.sh | awk -F'/' '/ip:/{if ($2+0 > 0) print $2+0; else print 64}'";

static const char GET_DYNAMIC_IPV6_ADDRESS[] =
    "get_dynamic_ipv6.sh | awk '/ip:/{split($2, a, \"/\"); print a[1]}'";

static const char GET_DYNAMIC_IPV6_GATEWAY[] =
    "get_dynamic_ipv6.sh | awk '/gateway:/{print $2}'";

static const char GET_STATIC_IPV6_PREFIX_LENGTH[] =
    "get_static_ipv6.sh | awk -F'/' '/ip:/{if ($2+0 > 0) print $2+0; else print 64}'";

static const char GET_STATIC_IPV6_ADDRESS[] =
    "get_static_ipv6.sh | awk '/ip:/{split($2, a, \"/\"); print a[1]}'";

static const char GET_STATIC_IPV6_GATEWAY[] =
    "get_static_ipv6.sh | awk '/gateway:/{print $2}'";

/** private variables */
static config_data_st s_configData = {};

/** private declarations */
static int readConfig(void);
static int readSerialConfig(void);
static int readNetworkConfig(void);
static int readCertificateConfig(void);


static void performGetEthernet();
static void performGetDhcp();
static void performGetSsh();
static void performGetHttp();
static void performGetDns();
static void performGetNtp();
static void performGetIpv6();

int monitor_config(void)
{
    int r = -1;
    time_t now = time(NULL);

    if (0 == readConfig())
    {
        r = sendConfigUpdate(&s_configData, now);
    }

    return r;
}

static int readConfig(void)
{
    (void)readSerialConfig();
    (void)readNetworkConfig();
    (void)readCertificateConfig();

    return 0;
}

static int readSerialConfig(void)
{
    char buffer[STRING_BUFFER_SIZE_M] = {0};

    /* TODO: fix this, get info from system */

    /* Debug Port */
    (void)sprintf(&s_configData.s_physical.s_serial[0].s_portname[0], "%s", "TTYS2");
    s_configData.s_physical.s_serial[0].s_config.u32_baudrate = 115200;
    (void)sprintf(&s_configData.s_physical.s_serial[0].s_config.s_connector[0], "%s", "RJ45");

    s_configData.s_physical.s_serial[0].s_config.u8_dataBits = 8;
    s_configData.s_physical.s_serial[0].s_config.u8_stopBits = 1;

    (void)sprintf(&s_configData.s_physical.s_serial[0].s_config.s_flowControl[0], "%s", "NONE");
    (void)sprintf(&s_configData.s_physical.s_serial[0].s_config.s_parity[0], "%s", "NONE");
    (void)sprintf(&s_configData.s_physical.s_serial[0].s_config.s_signalType[0], "%s", "RS232");

    /* Modbus */
    (void)sprintf(&s_configData.s_physical.s_serial[1].s_portname[0], "%s", "TTYS0");
    s_configData.s_physical.s_serial[1].s_config.u32_baudrate = 19200;
    (void)sprintf(&s_configData.s_physical.s_serial[1].s_config.s_connector[0], "%s", "OEM");

    s_configData.s_physical.s_serial[1].s_config.u8_dataBits = 8;
    s_configData.s_physical.s_serial[1].s_config.u8_stopBits = 1;

    (void)sprintf(&s_configData.s_physical.s_serial[1].s_config.s_flowControl[0], "%s", "NONE");
    (void)sprintf(&s_configData.s_physical.s_serial[1].s_config.s_parity[0], "%s", "EVEN");
    (void)sprintf(&s_configData.s_physical.s_serial[1].s_config.s_signalType[0], "%s", "RS485");

    return 0;
}

static int readCertificateConfig()
{
    char buffer[STRING_BUFFER_SIZE_MAX] = {0};
    char strbuf[STRING_BUFFER_SIZE_MAX] = {0};
    char cmdBuffer[STRING_BUFFER_SIZE_MAX] = {0};

    struct tm tm = {0};
    char outputTime[30];

    // type
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^type:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.type[0], "%s", &strbuf[0]);
    }
    
    // valid_from
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^valid-from:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        strptime(strbuf, "%Y-%m-%d %H:%M", &tm);
        strftime(outputTime, sizeof(outputTime), "%Y-%m-%dT%H:%M:%SZ", &tm);
        (void)sprintf(&s_configData.s_certificate.s_certdata.valid_from[0], "%s", &outputTime[0]);
    }
        
    // valid_to
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^valid-to:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        strptime(strbuf, "%Y-%m-%d %H:%M", &tm);
        strftime(outputTime, sizeof(outputTime), "%Y-%m-%dT%H:%M:%SZ", &tm);
        (void)sprintf(&s_configData.s_certificate.s_certdata.valid_to[0], "%s", &outputTime[0]);
    }
        
    // serial_number
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^serial-number:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.serial_number[0], "%s", &strbuf[0]);
    }

    // signature_algo
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^signature-algo:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.signature_algo[0], "%s", &strbuf[0]);
    }

    // key_algo
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^key-algo:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.key_algo[0], "%s", &strbuf[0]);
    }

    // raw text
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk '/^raw-text:/ {flag=1; sub(/^raw-text: /, \"\"); print; next}/-----END CERTIFICATE-----/ {print; flag=0}flag'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.raw_text[0], "%s", &strbuf[0]);
    }

    // subject_common_name
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^subject-common-name:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.subject.common_name[0], "%s", &strbuf[0]);
    }

    // subject_organization
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^subject-organization:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.subject.organization[0], "%s", &strbuf[0]);
    }

    // subject_country
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^subject-country:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.subject.country[0], "%s", &strbuf[0]);
    }

    // issuer_common_name
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^issuer-common-name:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.issuer.common_name[0], "%s", &strbuf[0]);
    }

    // issuer_organization
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^issuer-organization:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.issuer.organization[0], "%s", &strbuf[0]);
    }

    // issuer_country
    strcpy(cmdBuffer,GET_HTTPS_CERT_INFO);
    strcat(cmdBuffer, " | awk -F',' '/^issuer-country:/ {print $1}' | awk -F': ' '{print $2}'");
    if (0 == exec_cmd(cmdBuffer, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_certificate.s_certdata.issuer.country[0], "%s", &strbuf[0]);
    }

    return 0;
}

static int readNetworkConfig(void)
{

    /** ethernet */
    performGetEthernet();

    /** dhcp */
    performGetDhcp();

    /** ssh */
    performGetSsh();

    /** http */
    performGetHttp();

    /** dns */
    performGetDns();

    /** ntp */
    performGetNtp();

    /* Ipv6 */
    performGetIpv6();

    return 0;
}

static void performGetEthernet() //NOSONAR
{
    char buffer[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char strbuf[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};

    (void)sprintf(&s_configData.s_physical.s_network.s_portname[0], "%s", "ETH0");

    if (0 == exec_cmd(GET_HOSTNAME_SCRIPT, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_physical.s_network.s_config.s_hostName[0], "%s", &strbuf[0]);
    }

    if (0 == exec_cmd(GET_ETHERNET_MAC_ADDRESS_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_physical.s_network.s_config.s_macAddress[0], "%s", &strbuf[0]);
    }

    if (0 == exec_cmd(GET_ETHERNET_NEGOTIATE_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if (0 == strcmp("on", buffer))
        {
            s_configData.s_physical.s_network.s_config.b_autoNegotiate = true;
        }
        else
        {
            s_configData.s_physical.s_network.s_config.b_autoNegotiate = false;
        }
    }

    if (0 == exec_cmd(GET_ETHERNET_MTU_SIZE_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        s_configData.s_physical.s_network.s_config.sz_mtuSize = atoi(buffer);
    }

    if (0 == exec_cmd(GET_ETHERNET_SPEED_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        s_configData.s_physical.s_network.s_config.u16_speedMbps = (uint16_t)atoi(buffer);
    }
}
#ifndef USE_DEFAULT
/*
typedef struct {
    int  enabled;
    char address[OSAL_ADDR_LEN];
    char gateway[OSAL_ADDR_LEN];
    int  prefix_length;
} osal_ip_info_t;
OSAL_API int osal_get_static_info(int family, osal_ip_info_t *out);
OSAL_API int osal_get_dynamic_info(int family, osal_ip_info_t *out);

OSAL_API int osal_get_dynamic_info(int family, osal_ip_info_t *out)
{
    osal_runtime_t r;
    const char *cidr, *gw;
    int p, rc;

    if (!out || (family != OSAL_V4 && family != OSAL_V6))
        return osal_fail(OSAL_ERR_INVALID, "family must be 4 or 6");
    rc = osal_get_runtime(&r);
    if (rc) return rc;
    cidr = family == OSAL_V4 ? r.ipv4 : r.ipv6;
    gw = family == OSAL_V4 ? r.ipv4_gateway : r.ipv6_gateway;
    memset(out, 0, sizeof(*out));
    if (!cidr[0]) return OSAL_OK;
    p = osal_cidr_prefix(cidr);
    if (p < 0) return p;
    split_cidr(cidr, out->address, sizeof(out->address), &out->prefix_length);
    return copy_string(out->gateway, sizeof(out->gateway), gw, "gateway");
}

OSAL_API int osal_get_static_info(int family, osal_ip_info_t *out)
{
    osal_net_config_t c;
    const osal_static_t *s;
    int rc, p;

    if (!out || (family != OSAL_V4 && family != OSAL_V6))
        return osal_fail(OSAL_ERR_INVALID, "family must be 4 or 6");
    rc = osal_config_load(&c, NULL);
    if (rc) return rc;
    s = family == OSAL_V4 ? &c.ipv4.st : &c.ipv6.st;
    memset(out, 0, sizeof(*out));
    out->enabled = s->enabled;
    if (s->address[0]) {
        p = osal_cidr_prefix(s->address);
        if (p < 0) return p;
        split_cidr(s->address, out->address, sizeof(out->address), &out->prefix_length);
    }
    rc = copy_string(out->gateway, sizeof(out->gateway), s->gateway, "gateway");
    if (rc) return rc;
    return osal_get_static_status(&out->enabled);
}
*/
static void performGetDhcp() //NOSONAR
{
    osal_ip_info_t info;
    if (OSAL_OK != osal_get_dynamic_info(OSAL_V4, &info))
    {
        ERROR_PRINT("Failed to get dynamic IPv4 info: %s\n", osal_last_error());
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_ipAddress[0], "%s", "");
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_gateway[0], "%s", "");
        s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_subnetMask = 0;
    }
    else
    {
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_ipAddress[0], "%s", info.address);
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_gateway[0], "%s", info.gateway);
        s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_subnetMask = info.prefix_length;
    } 

    if (OSAL_OK != osal_get_static_info(OSAL_V4, &info))
    {
        ERROR_PRINT("Failed to get static IPv4 info: %s\n", osal_last_error());
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_ipAddress[0], "%s", "");
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_gateway[0], "%s", "");
        s_configData.s_physical.s_network.s_config.ipv4_static.s_subnetMask =  0;
    }
    else
    {
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_ipAddress[0], "%s", info.address);
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_gateway[0], "%s", info.gateway);
        s_configData.s_physical.s_network.s_config.ipv4_static.s_subnetMask =  info.prefix_length;
    }

    ERROR_PRINT("DHCP IPv4 dynamic: %s, gateway: %s, prefix length: %d\nStatic: %s, gateway: %s, prefix length: %d\n",
                s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_ipAddress,
                s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_gateway,
                s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_subnetMask,
                s_configData.s_physical.s_network.s_config.ipv4_static.s_ipAddress,
                s_configData.s_physical.s_network.s_config.ipv4_static.s_gateway,
                s_configData.s_physical.s_network.s_config.ipv4_static.s_subnetMask);

    s_configData.s_physical.ETH0.enabled = MONITOR_CONFIG_NETWORK_ETH0_ENABLED_DEFAULT;
    s_configData.s_physical.ETH0.writeable = MONITOR_CONFIG_NETWORK_ETH0_WRITEABLE_DEFAULT;
    
    s_configData.s_network.dhcp.writeable = MONITOR_CONFIG_NETWORK_DHCP_WRITEABLE_DEFAULT;
    s_configData.s_network.dhcp.port = MONITOR_CONFIG_NETWORK_DHCP_PORT_DEFAULT;
}

static void performGetIpv6() {

    osal_ip_info_t info;
    if (OSAL_OK != osal_get_dynamic_info(OSAL_V6, &info))
    {
        ERROR_PRINT("Failed to get dynamic IPv6 info: %s\n", osal_last_error());
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress[0], "%s", "");
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway[0], "%s", "");
        s_configData.s_physical.s_network.s_config.ipv6_dynamic.u8_prefixLength = 0;
    }
    else
    {
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress[0], "%s", info.address);
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway[0], "%s", info.gateway);
        s_configData.s_physical.s_network.s_config.ipv6_dynamic.u8_prefixLength = info.prefix_length;
    }

    if (OSAL_OK != osal_get_static_info(OSAL_V6, &info))
    {
        ERROR_PRINT("Failed to get static IPv6 info: %s\n", osal_last_error());
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_static.s_ipAddress[0], "%s", "");
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_static.s_gateway[0], "%s", "");
        s_configData.s_physical.s_network.s_config.ipv6_static.u8_prefixLength = 0;
    }
    else
    {
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_static.s_ipAddress[0], "%s", info.address);
        (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv6_static.s_gateway[0], "%s", info.gateway);
        s_configData.s_physical.s_network.s_config.ipv6_static.u8_prefixLength = info.prefix_length;
    }

    ERROR_PRINT("DHCP IPv6 dynamic: %s, gateway: %s, prefix length: %d\nStatic: %s, gateway: %s, prefix length: %d\n",
                s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress,
                s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway,
                s_configData.s_physical.s_network.s_config.ipv6_dynamic.u8_prefixLength,
                s_configData.s_physical.s_network.s_config.ipv6_static.s_ipAddress,
                s_configData.s_physical.s_network.s_config.ipv6_static.s_gateway,
                s_configData.s_physical.s_network.s_config.ipv6_static.u8_prefixLength);
}

static void performGetSsh()
{

    s_configData.s_network.ssh.writeable = MONITOR_CONFIG_NETWORK_SSH_WRITEABLE_DEFAULT;
    s_configData.s_network.ssh.port = MONITOR_CONFIG_NETWORK_SSH_PORT_DEFAULT;

    int active = 0;
    if (OSAL_OK != osal_get_ssh_status(&active))
    {
        ERROR_PRINT("Failed to get SSH status: %s\n", osal_last_error());
    }

    s_configData.s_network.ssh.enabled = (active == 1);

    ERROR_PRINT("SSH status: %s\n", s_configData.s_network.ssh.enabled ? "enabled" : "disabled");
}
#else

static void performGetDhcp() //NOSONAR
{
    char buffer[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char strbuf[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char netmaskbuf[STRING_BUFFER_SIZE_XS]       = {0};

    if (0 == exec_cmd(GET_ETHERNET_DYNAMIC_SUBNET_MASK_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if(strstr(buffer,"None")){
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_subnetMask[0], "%s", "");
        }
        else
        {
            calculateCidr(atoi(buffer), netmaskbuf);
            (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&netmaskbuf[0], '\n'));
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_subnetMask[0], "%s", &strbuf[0]);
        }
    }
    
    if (0 == exec_cmd(GET_ETHERNET_DYNAMIC_IP_ADDRESS_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if( strstr(buffer, "None") )
        {
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_ipAddress[0], "%s", "");
        }
        else
        {
            (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_ipAddress[0], "%s", &strbuf[0]);
        }
    }

    if (0 == exec_cmd(GET_ETHERNET_DYNAMIC_GATEWAY_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if( strstr(buffer, "None") )
        {
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_gateway[0], "%s", "");
        }
        else
        {
            (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_dynamic.s_gateway[0], "%s", &strbuf[0]);
        }
    }
    
    if (0 == exec_cmd(GET_ETHERNET_STATIC_IP_ADDRESS_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if(strstr(buffer,"None")){
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_ipAddress[0], "%s", "");
        }
        else
        {
            (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_ipAddress[0], "%s", &strbuf[0]);
        }
    }

    if (0 == exec_cmd(GET_ETHERNET_STATIC_GATEWAY_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if( strstr(buffer, "None") )
        {
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_gateway[0], "%s", "");
        }
        else
        {
            (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_gateway[0], "%s", &strbuf[0]);
        }
    }

    if (0 == exec_cmd(GET_ETHERNET_STATIC_SUBNET_MASK_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if(strstr(buffer,"None")){
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_subnetMask[0], "%s", "");
        }
        else{
            calculateCidr(atoi(buffer), netmaskbuf);
            (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&netmaskbuf[0], '\n'));
            (void)sprintf(&s_configData.s_physical.s_network.s_config.ipv4_static.s_subnetMask[0], "%s", &strbuf[0]);
        }
    }

    if (0 == exec_cmd(GET_ETHERNET_DHCP_STATUS_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if( strstr(buffer, "enabled") )
        {            
            s_configData.s_network.dhcp.enabled = true;
        }
        else
        {
            s_configData.s_network.dhcp.enabled = false;
        }
    }

    s_configData.s_physical.ETH0.enabled = MONITOR_CONFIG_NETWORK_ETH0_ENABLED_DEFAULT;
    s_configData.s_physical.ETH0.writeable = MONITOR_CONFIG_NETWORK_ETH0_WRITEABLE_DEFAULT;
    
    s_configData.s_network.dhcp.writeable = MONITOR_CONFIG_NETWORK_DHCP_WRITEABLE_DEFAULT;
    s_configData.s_network.dhcp.port = MONITOR_CONFIG_NETWORK_DHCP_PORT_DEFAULT;
}

static void performGetIpv6() {

    char buffer[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char strbuf[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char netmaskbuf[STRING_BUFFER_SIZE_XS]       = {0};

    if (0 == exec_cmd(GET_DYNAMIC_IPV6_PREFIX_LENGTH, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if(strstr(buffer,"None")){
            s_configData.s_physical.s_network.s_config.ipv6_dynamic.u8_prefixLength = 0;
        }
        else
        {
            s_configData.s_physical.s_network.s_config.ipv6_dynamic.u8_prefixLength = (uint8_t)atoi(buffer);
        }
    }

    if (0 == exec_cmd(GET_DYNAMIC_IPV6_ADDRESS, buffer, ARRAY_SIZE(buffer)))
    {
        if (strstr(buffer, "None"))
        {
            s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress[0] = '\0';     
       
        } else  {
           
            char *trimmed = trimTrailingCharacter(buffer, '\n');

            strncpy(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress, 
                    trimmed, 
                    sizeof(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress) - 1);
                    
            s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress[sizeof(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress) - 1] = '\0';
        }
        
    }

    if (0 == exec_cmd(GET_DYNAMIC_IPV6_GATEWAY, buffer, ARRAY_SIZE(buffer)))
    {
        if (strstr(buffer, "None"))
        {
            s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway[0] = '\0';     
       
        }  else   {

            char *trimmed = trimTrailingCharacter(buffer, '\n');
            
            strncpy(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway, 
                    trimmed, 
                    sizeof(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway) - 1);
                    
            s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway[sizeof(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway) - 1] = '\0';
        }
        
    }

    if (0 == exec_cmd(GET_STATIC_IPV6_PREFIX_LENGTH, &buffer[0], ARRAY_SIZE(buffer)))
    {
        if(strstr(buffer,"None")){
            s_configData.s_physical.s_network.s_config.ipv6_static.u8_prefixLength = 0;
       
        }   else  {
            s_configData.s_physical.s_network.s_config.ipv6_static.u8_prefixLength = (uint8_t)atoi(buffer);
        }

    }


    if (0 == exec_cmd(GET_STATIC_IPV6_ADDRESS, buffer, ARRAY_SIZE(buffer)))
    {
        if (strstr(buffer, "None"))
        {
            s_configData.s_physical.s_network.s_config.ipv6_static.s_ipAddress[0] = '\0';     
        
        }  else  {

            char *trimmed = trimTrailingCharacter(buffer, '\n');
            strncpy(s_configData.s_physical.s_network.s_config.ipv6_static.s_ipAddress, 
                    trimmed, 
                    sizeof(s_configData.s_physical.s_network.s_config.ipv6_static.s_ipAddress) - 1);

            s_configData.s_physical.s_network.s_config.ipv6_static.s_ipAddress[sizeof(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_ipAddress) - 1] = '\0';
        }
        
    }

    if (0 == exec_cmd(GET_STATIC_IPV6_GATEWAY, buffer, ARRAY_SIZE(buffer)))
    {
        if (strstr(buffer, "None"))
        {
            s_configData.s_physical.s_network.s_config.ipv6_static.s_gateway[0] = '\0';     
    
        }  else   {
    
            char *trimmed = trimTrailingCharacter(buffer, '\n');
            strncpy(s_configData.s_physical.s_network.s_config.ipv6_static.s_gateway, 
                    trimmed, 
                    sizeof(s_configData.s_physical.s_network.s_config.ipv6_static.s_gateway) - 1);
            s_configData.s_physical.s_network.s_config.ipv6_static.s_gateway[sizeof(s_configData.s_physical.s_network.s_config.ipv6_dynamic.s_gateway) - 1] = '\0';
        }
    }
}

static void performGetSsh()
{
    char buffer[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char strbuf[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};

    s_configData.s_network.ssh.writeable = MONITOR_CONFIG_NETWORK_SSH_WRITEABLE_DEFAULT;
    s_configData.s_network.ssh.port = MONITOR_CONFIG_NETWORK_SSH_PORT_DEFAULT;

    if (0 == exec_cmd(GET_SSH_STATUS_SCRIPT, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));

        if (strstr(strbuf, "enabled"))
        {
            s_configData.s_network.ssh.enabled = true;
        }
        else
        {
            s_configData.s_network.ssh.enabled = false;
        }
    }
}
#endif

static void performGetHttp()
{
    char buffer[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char strbuf[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};

    s_configData.s_network.http.writeable = MONITOR_CONFIG_NETWORK_HTTP_WRITEABLE_DEFAULT;
    s_configData.s_network.http.port = MONITOR_CONFIG_NETWORK_HTTP_PORT_DEFAULT;

    if (0 == exec_cmd(GET_HTTP_STATUS_SCRIPT, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));

        if (strstr(strbuf, "enabled"))
        {
            s_configData.s_network.http.enabled = true;
        }
        else
        {
            s_configData.s_network.http.enabled = false;
        }
    }

    /** https */
    s_configData.s_network.https.enabled = MONITOR_CONFIG_NETWORK_HTTPS_ENABLED_DEFAULT;
    s_configData.s_network.https.writeable = MONITOR_CONFIG_NETWORK_HTTPS_WRITEABLE_DEFAULT;
    s_configData.s_network.https.port = MONITOR_CONFIG_NETWORK_HTTPS_PORT_DEFAULT;
}
#ifndef USE_DEFAULT
/*
OSAL_API int osal_get_dns_status(int *active); // 1: active, 0: inactive

typedef struct {
    int  enabled;
    int  count;
    char server[OSAL_MAX_SERVERS][OSAL_HOST_LEN];
} osal_server_list_t;

OSAL_API int osal_get_dns_config(osal_server_list_t *out); // e.g. 1.1.1.1 8.8.8.8

typedef struct
{
    bool enabled;
    bool writeable;
    uint16_t port;

    struct DnsConfig
    {
        char s_serverNames[1024]; // space delimiter
    } s_config;
} s_networkDnsConfig;
*/
static void performGetDns()
{
    s_configData.s_network.dns.writeable = MONITOR_CONFIG_NETWORK_DNS_WRITEABLE_DEFAULT;
    s_configData.s_network.dns.port = MONITOR_CONFIG_NETWORK_DNS_PORT_DEFAULT;

    int active = 0;
    if (OSAL_OK != osal_get_dns_status(&active))
    {
        ERROR_PRINT("osal_get_dns_status failed: %s\n", osal_last_error());
    }

    s_configData.s_network.dns.enabled = (active == 1);

    osal_server_list_t server_list = {0};
    if (OSAL_OK != osal_get_dns_config(&server_list))
    {
        (void)memset(&s_configData.s_network.dns.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.dns.s_config.s_serverNames));
        ERROR_PRINT("osal_get_dns_config failed: %s\n", osal_last_error());
    }
    else
    {
        (void)memset(&s_configData.s_network.dns.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.dns.s_config.s_serverNames));
        for (int i = 0; i < server_list.count && i < OSAL_MAX_SERVERS; ++i)
        {
            if (i > 0)
            {
                (void)strcat(s_configData.s_network.dns.s_config.s_serverNames, " ");
            }
            (void)strcat(s_configData.s_network.dns.s_config.s_serverNames, server_list.server[i]);
        }
    }

    ERROR_PRINT("[%s]: active: %d\tserver_list: %s\n", __func__, active, s_configData.s_network.dns.s_config.s_serverNames);


    // if (0 == exec_cmd(GET_DNS_STATUS_SCRIPT, &buffer[0], ARRAY_SIZE(buffer)))
    // {
    //     (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));

    //     if (strstr(strbuf, "enabled"))
    //     {
    //         s_configData.s_network.dns.enabled = true;
    //     }
    //     else
    //     {
    //         s_configData.s_network.dns.enabled = false;
    //     }
    // }

    // if (0 == exec_cmd(GET_DNS_SERVER_LIST_CMD, &buffer[0], ARRAY_SIZE(buffer))) // e.g. 1.1.1.1 8.8.8.8
    // {
    //     // TODO: fix, split data
    //     (void)sprintf(&strbuf[0], "%s", removeCharacter(&buffer[0], '\n'));
    //     (void)sprintf(&s_configData.s_network.dns.s_config.s_serverNames[0], "%s", &strbuf[0]);
    // }
    // else
    // {
    //     memset(&s_configData.s_network.dns.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.dns.s_config.s_serverNames));
    // }
}

static void performGetNtp()
{
    s_configData.s_network.ntp.writeable = MONITOR_CONFIG_NETWORK_NTP_WRITEABLE_DEFAULT;
    s_configData.s_network.ntp.port = MONITOR_CONFIG_NETWORK_NTP_PORT_DEFAULT;

    int active = 0;
    if (OSAL_OK != osal_get_ntp_status(&active))
    {
        ERROR_PRINT("osal_get_ntp_status failed: %s\n", osal_last_error());
    }

    s_configData.s_network.ntp.enabled = (active == 1);

    osal_server_list_t server_list = {0};
    if (OSAL_OK != osal_get_ntp_config(&server_list))
    {
        memset(&s_configData.s_network.ntp.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.ntp.s_config.s_serverNames));
        ERROR_PRINT("osal_get_dns_config failed: %s\n", osal_last_error());
    }
    else
    {
        (void)memset(&s_configData.s_network.ntp.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.ntp.s_config.s_serverNames));
        for (int i = 0; i < server_list.count && i < OSAL_MAX_SERVERS; ++i)
        {
            if (i > 0)
            {
                (void)strcat(s_configData.s_network.ntp.s_config.s_serverNames, " ");
            }
            (void)strcat(s_configData.s_network.ntp.s_config.s_serverNames, server_list.server[i]);
        }
    }

    ERROR_PRINT("[%s]: active: %d\tserver_list: %s\n", __func__, active, s_configData.s_network.ntp.s_config.s_serverNames);
    // if (0 == exec_cmd(GET_NTP_STATUS_SCRIPT, &buffer[0], ARRAY_SIZE(buffer)))
    // {
    //     (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));

    //     if (strstr(strbuf, "enabled"))
    //     {
    //         s_configData.s_network.ntp.enabled = true;
    //     }
    //     else
    //     {
    //         s_configData.s_network.ntp.enabled = false;
    //     }
    // }

    // if (0 == exec_cmd(GET_NTP_SERVER_LIST_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    // {
    //     // TODO: fix, split data
    //     (void)sprintf(&strbuf[0], "%s", removeCharacter(&buffer[0], '\n'));
    //     (void)sprintf(&s_configData.s_network.ntp.s_config.s_serverNames[0], "%s", &strbuf[0]);
    // }
    // else
    // {
    //     memset(&s_configData.s_network.ntp.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.ntp.s_config.s_serverNames));
    // }

    s_configData.s_network.ssdp.enabled = MONITOR_CONFIG_NETWORK_SSDP_ENABLED_DEFAULT;
    s_configData.s_network.ssdp.writeable = MONITOR_CONFIG_NETWORK_SSDP_WRITEABLE_DEFAULT;
    s_configData.s_network.ssdp.port = MONITOR_CONFIG_NETWORK_SSDP_PORT_DEFAULT;
    (void)sprintf(&s_configData.s_network.ssdp.s_config.s_ipV6Scope[0], "%s", "LINK");
    s_configData.s_network.ssdp.s_config.u16_multicastInterval = 600;
    s_configData.s_network.ssdp.s_config.u16_ttl = 5;
}
#else
static void performGetDns()
{
    char buffer[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char strbuf[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};

    s_configData.s_network.dns.writeable = MONITOR_CONFIG_NETWORK_DNS_WRITEABLE_DEFAULT;
    s_configData.s_network.dns.port = MONITOR_CONFIG_NETWORK_DNS_PORT_DEFAULT;

    if (0 == exec_cmd(GET_DNS_STATUS_SCRIPT, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));

        if (strstr(strbuf, "enabled"))
        {
            s_configData.s_network.dns.enabled = true;
        }
        else
        {
            s_configData.s_network.dns.enabled = false;
        }
    }

    if (0 == exec_cmd(GET_DNS_SERVER_LIST_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        // TODO: fix, split data
        (void)sprintf(&strbuf[0], "%s", removeCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_network.dns.s_config.s_serverNames[0], "%s", &strbuf[0]);
    }
    else
    {
        memset(&s_configData.s_network.dns.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.dns.s_config.s_serverNames));
    }
}

static void performGetNtp()
{
    char buffer[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};
    char strbuf[NETWORK_CONFIG_CHAR_BUFFER_SIZE] = {0};

    s_configData.s_network.ntp.writeable = MONITOR_CONFIG_NETWORK_NTP_WRITEABLE_DEFAULT;
    s_configData.s_network.ntp.port = MONITOR_CONFIG_NETWORK_NTP_PORT_DEFAULT;

    if (0 == exec_cmd(GET_NTP_STATUS_SCRIPT, &buffer[0], ARRAY_SIZE(buffer)))
    {
        (void)sprintf(&strbuf[0], "%s", trimTrailingCharacter(&buffer[0], '\n'));

        if (strstr(strbuf, "enabled"))
        {
            s_configData.s_network.ntp.enabled = true;
        }
        else
        {
            s_configData.s_network.ntp.enabled = false;
        }
    }

    if (0 == exec_cmd(GET_NTP_SERVER_LIST_CMD, &buffer[0], ARRAY_SIZE(buffer)))
    {
        // TODO: fix, split data
        (void)sprintf(&strbuf[0], "%s", removeCharacter(&buffer[0], '\n'));
        (void)sprintf(&s_configData.s_network.ntp.s_config.s_serverNames[0], "%s", &strbuf[0]);
    }
    else
    {
        memset(&s_configData.s_network.ntp.s_config.s_serverNames[0], 0, ARRAY_SIZE(s_configData.s_network.ntp.s_config.s_serverNames));
    }

    s_configData.s_network.ssdp.enabled = MONITOR_CONFIG_NETWORK_SSDP_ENABLED_DEFAULT;
    s_configData.s_network.ssdp.writeable = MONITOR_CONFIG_NETWORK_SSDP_WRITEABLE_DEFAULT;
    s_configData.s_network.ssdp.port = MONITOR_CONFIG_NETWORK_SSDP_PORT_DEFAULT;
    (void)sprintf(&s_configData.s_network.ssdp.s_config.s_ipV6Scope[0], "%s", "LINK");
    s_configData.s_network.ssdp.s_config.u16_multicastInterval = 600;
    s_configData.s_network.ssdp.s_config.u16_ttl = 5;
}
#endif
