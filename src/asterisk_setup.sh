#!/bin/bash
# Phone Bridge — Asterisk SIP server setup (Issue #9)
# Runs Asterisk in Docker on Mac, Linphone registers from Android phone

set -e

ASTERISK_DIR="$(dirname "$0")/../config/asterisk"
mkdir -p "$ASTERISK_DIR"

# Create minimal Asterisk config for SIP bridge
cat > "$ASTERISK_DIR/sip.conf" << 'SIP_EOF'
[general]
context=default
bindaddr=0.0.0.0
bindport=5060
transport=udp

[android-phone]
type=friend
secret=phonebridge123
host=dynamic
context=from-phone
qualify=yes
SIP_EOF

cat > "$ASTERISK_DIR/extensions.conf" << 'EXT_EOF'
[default]
; Inbound calls from Android phone → AI handler
exten => _X.,1,NoOp(Inbound call from ${CALLERID(num)})
 same => n,Answer()
 same => n,AGI(ai_call_handler.py)
 same => n,Hangup()

[from-phone]
; Outbound calls dialed from AI system
exten => _1XXXXXXXXXX,1,NoOp(Outbound to ${EXTEN})
 same => n,Dial(SIP/android-phone/${EXTEN})
 same => n,Hangup()
EXT_EOF

cat > "$ASTERISK_DIR/manager.conf" << 'MGR_EOF'
[general]
enabled=yes
bindaddr=127.0.0.1
port=5038

[phonebridge]
secret=bridgepass
read=all
write=all
MGR_EOF

echo "Asterisk config written to $ASTERISK_DIR"

# Docker run command (save for reference)
cat > "$(dirname "$0")/../config/asterisk/docker-run.sh" << 'DOCKER_EOF'
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
DOCKER_EOF
chmod +x "$(dirname "$0")/../config/asterisk/docker-run.sh"

echo "✅ Asterisk config complete"
echo "Next: run config/asterisk/docker-run.sh"
