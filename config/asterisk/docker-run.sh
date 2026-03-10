#!/bin/bash
# Run Asterisk SIP server in Docker
docker run -d \
  --name asterisk-bridge \
  --network host \
  -v "$(pwd)/config/asterisk:/etc/asterisk" \
  andrius/asterisk:latest

echo "Asterisk running. SIP port: 5060, AMI port: 5038"
echo "Configure Linphone on Android:"
echo "  SIP server: $(ipconfig getifaddr en0)"
echo "  Username: android-phone"
echo "  Password: phonebridge123"
