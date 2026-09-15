because I tried removing the *_null in the compile, still the same issue

cat .gitmodules
[submodule "os_stub/openssllib/openssl"]
path = os_stub/openssllib/openssl
url = https://github.com/openssl/openssl
[submodule "os_stub/mbedtlslib/mbedtls"]
path = os_stub/mbedtlslib/mbedtls
url = https://github.com/Mbed-TLS/mbedtls
[submodule "unit_test/cmockalib/cmocka"]
path = unit_test/cmockalib/cmocka
url = https://gitlab.com/cmocka/cmocka.git
╭─░▒▓    ~/libspdm-native/libspdm    main ···························································· ✔  system   01:40:14 PM  ▓▒░
╰─ ls
CMakeLists.txt LICENSE.md SECURITY.md build include libspdm.pc.in script
CONTRIBUTING.md README.md VERSION.md doc library os_stub unit_test
╭─░▒▓    ~/libspdm-native/libspdm    main ···························································· ✔  system   01:40:18 PM  ▓▒░
╰─ ls os_stub
armbuild_lib debuglib mbedtlslib platform_lib_null spdm_device_secret_lib_null
cryptlib_mbedtls debuglib_null memlib rnglib spdm_device_secret_lib_sample
cryptlib_null include openssllib spdm_cert_verify_callback_sample spdm_device_secret_lib_tpm
cryptlib_openssl malloclib platform_lib spdm_crypt_ext_lib

/home/melvin/libspdm-native/libspdm
maybe just use local path like this in -l?
