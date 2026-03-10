package com.phonebridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.telephony.SmsMessage;
import android.util.Log;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class SmsReceiver extends BroadcastReceiver {
    private static final String TAG = "PhoneBridge";
    // Mac IP — set via adb shell am broadcast to update at runtime
    static String MAC_URL = "http://192.168.1.235:3001/webhook/sms";

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (action == null) return;

        if (!action.equals("android.provider.Telephony.SMS_RECEIVED") &&
            !action.equals("android.provider.Telephony.SMS_DELIVER")) {
            return;
        }

        Bundle bundle = intent.getExtras();
        if (bundle == null) return;

        Object[] pdus = (Object[]) bundle.get("pdus");
        if (pdus == null || pdus.length == 0) return;

        String format = bundle.getString("format");
        StringBuilder body = new StringBuilder();
        String sender = null;

        for (Object pdu : pdus) {
            SmsMessage msg = SmsMessage.createFromPdu((byte[]) pdu, format);
            if (msg == null) continue;
            if (sender == null) sender = msg.getDisplayOriginatingAddress();
            body.append(msg.getDisplayMessageBody());
        }

        if (sender == null) return;

        Log.i(TAG, "SMS from " + sender + ": " + body.toString().substring(0, Math.min(50, body.length())));

        // Forward to Mac asynchronously
        final String finalSender = sender;
        final String finalBody = body.toString();
        new Thread(() -> forwardToMac(finalSender, finalBody)).start();
    }

    private void forwardToMac(String sender, String body) {
        try {
            // Escape JSON
            String jsonBody = body.replace("\\", "\\\\").replace("\"", "\\\"")
                                  .replace("\n", "\\n").replace("\r", "\\r");
            String json = "{\"phoneNumber\":\"" + sender + "\",\"message\":\"" + jsonBody + "\"}";

            URL url = new URL(MAC_URL);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);

            try (OutputStream os = conn.getOutputStream()) {
                os.write(json.getBytes(StandardCharsets.UTF_8));
            }

            int code = conn.getResponseCode();
            Log.i(TAG, "Forwarded to Mac: HTTP " + code);
            conn.disconnect();
        } catch (Exception e) {
            Log.e(TAG, "Forward failed: " + e.getMessage());
        }
    }
}
