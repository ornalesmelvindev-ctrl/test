currently, this is what super_combined.sh
#!/bin/bash

# Definition of subnets to test
SUBNETS=("10.152.12" "10.152.128")

PASSWORD='gEd2Be&v'
USERNAME="root"
TIMEOUT=2

# Load user mappings from /etc/users_ip
declare -A USER_MAP

update_user_ip.sh >/dev/null 2>&1

if [ -f /etc/users_ip ]; then
	while IFS='=' read -r USER IP; do
		[[ -z "$USER" || -z "$IP" ]] && continue
		[[ "$USER" =~ ^# ]] && continue

		USER=$(echo "$USER" | xargs)
		IP=$(echo "$IP" | xargs)

		USER_MAP["$IP"]="$USER"
	done </etc/users_ip
fi

if ! command -v sshpass >/dev/null 2>&1; then
	echo "Error: 'sshpass' is not installed."
	exit 1
fi

RESULTS_FILE=$(mktemp)

echo
echo "🔍 Scanning Devices..."
echo

printf "+-----------------+-----------------+-------+---------+--------------------------------+\n"
printf "| %-15s | %-15s | %-5s | %-7s | %-30s |\n" \
	"IP Address" "Variant" "SSH" "Redfish" "User(s)"
printf "+-----------------+-----------------+-------+---------+--------------------------------+\n"

for SUBNET in "${SUBNETS[@]}"; do
	for i in {1..254}; do

		# Skip .1 but allow .21
		[ "$i" -eq 1 ] && continue

		TARGET_IP="${SUBNET}.${i}"

		(
			SSH_STATUS="No"
			REDFISH_STATUS="No"
			MODEL="N/A"
			USER_NAME="N/A"

			#
			# Check Redfish
			#
			HTTP_CODE=$(curl -sk \
				--connect-timeout "$TIMEOUT" \
				-o /dev/null \
				-w "%{http_code}" \
				"https://${TARGET_IP}/redfish/v1/" 2>/dev/null)

			if [ "$HTTP_CODE" = "200" ]; then
				REDFISH_STATUS="Yes"
			fi

			#
			# Check SSH
			#
			PROFILE=$(sshpass -p "$PASSWORD" ssh \
				-o StrictHostKeyChecking=no \
				-o UserKnownHostsFile=/dev/null \
				-o ConnectTimeout="$TIMEOUT" \
				-o NumberOfPasswordPrompts=1 \
				-o PreferredAuthentications=password \
				"${USERNAME}@${TARGET_IP}" \
				"jq -r '.ae_modbus_device_profile.profile_name' /etc/ae-config/enabled/device-profile.cfg" \
				2>/dev/null)

			if [ $? -eq 0 ]; then
				SSH_STATUS="Yes"

				case "$PROFILE" in
				standard)
					MODEL="3kWh"
					;;
				hpr)
					MODEL="5kWh"
					;;
				hpr-v2)
					MODEL="12kW"
					;;
				*)
					MODEL="Unknown"
					;;
				esac

				USER_IPS=$(sshpass -p "$PASSWORD" ssh \
					-o StrictHostKeyChecking=no \
					-o UserKnownHostsFile=/dev/null \
					-o ConnectTimeout="$TIMEOUT" \
					-o NumberOfPasswordPrompts=1 \
					-o PreferredAuthentications=password \
					"${USERNAME}@${TARGET_IP}" \
					"who | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | sort -u" \
					2>/dev/null)

				if [ -n "$USER_IPS" ]; then
					USER_NAME=""

					while read -r IP; do
						[ -z "$IP" ] && continue

						if [ -n "${USER_MAP[$IP]}" ]; then
							USER_NAME+="${USER_MAP[$IP]}, "
						else
							USER_NAME+="${IP}, "
						fi
					done <<<"$USER_IPS"

					USER_NAME="${USER_NAME%, }"
				else
					USER_NAME="None"
				fi
			fi

			#
			# Show if either SSH or Redfish is available
			#
			if [ "$SSH_STATUS" = "Yes" ] || [ "$REDFISH_STATUS" = "Yes" ]; then
				printf "| %-15s | %-15s | %-5s | %-7s | %-30s |\n" \
					"$TARGET_IP" \
					"$MODEL" \
					"$SSH_STATUS" \
					"$REDFISH_STATUS" \
					"$USER_NAME" \
					>>"$RESULTS_FILE"
			fi

		) &
	done
done

wait

sort -V "$RESULTS_FILE"

FOUND=$(wc -l <"$RESULTS_FILE")

printf "+-----------------+-----------------+-------+---------+--------------------------------+\n"

echo
echo "Devices Found : $FOUND"
echo "✅ Scan complete."

rm -f "$RESULTS_FILE"


output
super_combined.sh
[sudo] password for melvin:

🔍 Scanning Devices...

+-----------------+-----------------+-------+---------+--------------------------------+
| IP Address      | Variant         | SSH   | Redfish | User(s)                        |
+-----------------+-----------------+-------+---------+--------------------------------+
| 10.152.12.43    | 12kW            | Yes   | No      | None                           |
| 10.152.12.116   | 12kW            | Yes   | Yes     | None                           |
+-----------------+-----------------+-------+---------+--------------------------------+

Devices Found : 2
✅ Scan complete.

update_user_ip.sh
#!/bin/bash

TARGET_FILE="/etc/users_ip"
TEMP_FILE=$(mktemp)

# 1. Gather all currently logged-in unique users from 'who'
ACTIVE_USERS=$(who | awk '{print $1}' | sort -u)

# 2. Iterate through each unique user to extract their best IPv4 address
for USERNAME in $ACTIVE_USERS; do
	# Skip system/display managers if they appear
	[ "$USERNAME" = "misadmin" ] && continue

	IP=""

	# Attempt A: Look for a live SSH network socket matching the user
	# Filters out localhost/loopback mappings automatically
	IP=$(sudo ss -atpn | grep :22 | grep "pid=" | while read -r line; do
		PID=$(echo "$line" | sed -E 's/.*users:\(\(\"sshd\",pid=([0-9]+).*/\1/')
		CONN_USER=$(ps -o user= -p "$PID" 2>/dev/null | xargs)
		if [ "$CONN_USER" = "$USERNAME" ]; then
			# Extract foreign IPv4 field and strip out the port suffix
			echo "$line" | awk '{print $5}' | cut -d: -f1
			break
		fi
	done)

	# Attempt B: Fallback to 'last' log if the user has no live socket (e.g. detached tmux/screen)
	if [ -z "$IP" ] || [ "$IP" = "0.0.0.0" ] || [ "$IP" = "127.0.0.1" ]; then
		# Find the most recent login line that contains a valid external IPv4 address
		IP=$(last -i "$USERNAME" | grep -vE '0\.0\.0\.0|localhost|^wtmp' | head -n 1 | awk '{print $3}')
	fi

	# Only save valid, non-empty username-IP assignments
	if [ -n "$USERNAME" ] && [ -n "$IP" ]; then
		echo "${USERNAME}=${IP}" >>"$TEMP_FILE"
	fi
done

# 3. Safely swap or overwrite the target file using appropriate permissions
if [ -s "$TEMP_FILE" ]; then
	if [ -w "$TARGET_FILE" ] || [ ! -f "$TARGET_FILE" ]; then
		cat "$TEMP_FILE" >"$TARGET_FILE"
	else
		# Fallback to sudo if file permissions are restricted to root
		cat "$TEMP_FILE" | sudo tee "$TARGET_FILE" >/dev/null
	fi
fi

# Clean up temporary storage file
rm -f "$TEMP_FILE"

What I want is what if not use this build server scripts instead create local one, like const cmd or just create our own bash script, to be exe
cuteed at the build server? if possible, totally remove the super_combined.sh, I mean I think our local const cmd or bash script can be like just 1 script right?
Also, take note that writing on /etc required sudo so sshpass is needed there

also, I want the script to be dynamic, as well as the one being shown in the app
like for example, currently, I am just getting these
| IP Address      | Variant         | SSH   | Redfish | User(s)                        |
what if I add other field? so the UI must dynamically adjust, like the fields being displayed by yhe script must also be the fields be 
shown in the app.

well you can change the ouput of the script, so it can be easily me dynamic, like if you dont need these, just dont add on the print
🔍 Scanning Devices...

Devices Found : 2
✅ Scan complete.


like totally isolate our app, so this can be standalone, also like template where I can add script that will show something, then dyanmically
create in the app? Is it possible? if so may you please

wait, in the autosuggestion(in the build server part), if I typed just '/', nothing is being dropdown suggested like if /etc, or /usr, or /home,
then when I type just /ho, no dropdown is suggested like /home, can it be fixed? if so, include this as well, but if this will create too much
overhead on the app, just ignore