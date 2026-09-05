[launchInteractiveSsh] start wt.exe -w 0 nt --title "ecpsabuild" wsl.exe -e bash -c "$(echo c3NocGFzcyAtcCAnY2xlYXIxMicgc3NoIC1vIFN0cmljdEhvc3RLZXlDaGVja2luZz1ubyAtbyBVc2VyS25vd25Ib3N0c0ZpbGU9L2Rldi9udWxsIC1vIExvZ0xldmVsPUVSUk9SIC1vIFNlcnZlckFsaXZlSW50ZXJ2YWw9MTUgLW8gU2VydmVyQWxpdmVDb3VudE1heD0zICdtZWx2aW5AZWNwc2FidWlsZCc= | base64 -d)"
remote username contains invalid characters

[process exited with code 255 (0x000000ff)]
You can now close this terminal with Ctrl+D, or press Enter to restart.
remote username contains invalid characters

then in powershell
start wt.exe -w 0 nt --title "ecpsabuild" powershell.exe -NoExit -EncodedCommand JgAgACcAcwBzAGgAcABhAHMAcwAnACAALQBwACAAJwBjAGwAZQBhAHIAMQAyACcAIABzAHMAaAAgAC0AbwAgAFMAdAByAGkAYwB0AEgAbwBzAHQASwBlAHkAQwBoAGUAYwBrAGkAbgBnAD0AbgBvACAALQBvACAAVQBzAGUAcgBLAG4AbwB3AG4ASABvAHMAdABzAEYAaQBsAGUAPQAvAGQAZQB2AC8AbgB1AGwAbAAgAC0AbwAgAEwAbwBnAEwAZQB2AGUAbAA9AEUAUgBSAE8AUgAgAC0AbwAgAFMAZQByAHYAZQByAEEAbABpAHYAZQBJAG4AdABlAHIAdgBhAGwAPQAxADUAIAAtAG8AIABTAGUAcgB2AGUAcgBBAGwAaQB2AGUAQwBvAHUAbgB0AE0AYQB4AD0AMwAgACcAbQBlAGwAdgBpAG4AQABlAGMAcABzAGEAYgB1AGkAbABkACcA
[scan-pmcs] start 2026-09-05T23:19:15.700Z

what are you doing, can you just set back to default? take note the previous spawning a new windows terminal instead of using the existing one is working, I mean can you really fix it?
Also, make the app name as, tarball name and the extracted name as "SYSAPPS Dashboard"

can you make it working this time? I mean if you cant really fix it, go back to default, but take note the naming of the tabs is working okay?

but wait, I dont understand it, the ssh of devices(e.g. 10.152.12.12) is working on powershell, (but of course in wsl, still the invalid chars)
what may be the problem in ssh of the build server?

take note credentials were correct since I can scan on 
[scan-pmcs] done 2026-09-05T23:22:44.436Z 2 devices
[scan-pmcs] start 2026-09-05T23:22:45.285Z
[scan-pmcs] done 2026-09-05T23:22:53.283Z 2 devices
[scan-pmcs] start 2026-09-05T23:23:15.176Z
[scan-pmcs] done 2026-09-05T23:23:23.487Z 2 devices
[scan-pmcs] start 2026-09-05T23:23:45.190Z
[scan-pmcs] done 2026-09-05T23:23:54.684Z 2 devices