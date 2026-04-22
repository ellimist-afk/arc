# Twitch EventSub Webhook Setup Guide

This guide will help you set up EventSub webhooks to detect real Twitch ad breaks.

## Prerequisites

1. **Public HTTPS URL** - EventSub requires a publicly accessible HTTPS endpoint
2. **Twitch Application** - You need a registered Twitch app
3. **Port Forwarding** - Your webhook server needs to be accessible from the internet

## Setup Steps

### 1. Get a Public URL

You have several options:

#### Option A: ngrok (Easiest for testing)
1. Download ngrok from https://ngrok.com/
2. Sign up for a free account
3. Run: `ngrok http 8080`
4. Copy the HTTPS URL (e.g., `https://abc123.ngrok.io`)

#### Option B: Cloudflare Tunnel (Free, more stable)
1. Install cloudflared
2. Run: `cloudflared tunnel --url http://localhost:8080`
3. Copy the provided URL

#### Option C: Your Own Domain
1. Set up port forwarding on your router (port 8080)
2. Point a domain to your public IP
3. Use Let's Encrypt for HTTPS

### 2. Update Environment Variables

Add these to your `.env` file:

```
# EventSub Configuration
WEBHOOK_SECRET=your_random_secret_here_32chars_min
WEBHOOK_CALLBACK_URL=https://your-public-url.com/webhooks/callback
WEBHOOK_PORT=8080
```

Generate a secure webhook secret (minimum 32 characters):
```python
import secrets
print(secrets.token_urlsafe(32))
```

### 3. Update Bot Integration

Add EventSub to your bot.py:

```python
# In imports section
from twitch.eventsub_webhook import EventSubWebhook
from features.ad_announcer import AdAnnouncer

# In setup() method, after initializing Twitch client:

# Initialize EventSub webhook
if self.config.get('WEBHOOK_CALLBACK_URL'):
    logger.info("Initializing EventSub webhook...")
    self.eventsub = EventSubWebhook(
        client_id=self.config['TWITCH_CLIENT_ID'],
        client_secret=self.config['TWITCH_CLIENT_SECRET'],
        access_token=self.config['TWITCH_ACCESS_TOKEN'],
        webhook_secret=self.config.get('WEBHOOK_SECRET', 'change_this_to_random_secret'),
        callback_url=self.config['WEBHOOK_CALLBACK_URL'],
        port=int(self.config.get('WEBHOOK_PORT', 8080))
    )
    
    # Start webhook server
    await self.eventsub.start()
    
    # Initialize Ad Announcer
    self.ad_announcer = AdAnnouncer(
        twitch_client=self.twitch_client,
        audio_queue=self.audio_queue,
        response_coordinator=self.response_coordinator
    )
    
    # Register ad break handler
    self.eventsub.on_event('channel.ad_break.begin', self.ad_announcer.handle_ad_break_begin)
    
    logger.info("EventSub webhook ready for ad break events")
```

### 4. Required Twitch Scopes

Make sure your access token has these scopes:
- `channel:read:ads` - To receive ad break notifications

### 5. Testing

1. Start your bot with the webhook server
2. Check logs for "EventSub webhook server started"
3. Run an ad from your Twitch dashboard
4. The bot should announce the ad break in chat and voice

## Troubleshooting

### Webhook Not Receiving Events
- Check your public URL is accessible: `curl https://your-url.com/webhooks/callback`
- Verify ngrok/tunnel is still running
- Check firewall isn't blocking port 8080

### Invalid Signature Errors
- Make sure WEBHOOK_SECRET matches exactly
- Don't change the secret after subscriptions are created

### Subscription Failed
- Verify your access token has the required scope
- Check the broadcaster ID is correct
- Look for detailed error messages in logs

## Security Notes

1. **Never expose your webhook secret** - Keep it in .env
2. **Use HTTPS only** - Twitch requires HTTPS for webhooks
3. **Rotate secrets regularly** - Change webhook secret periodically
4. **Validate signatures** - The code already does this, don't disable it

## Production Deployment

For production use:
1. Use a stable public URL (not ngrok free tier)
2. Set up proper SSL certificates
3. Use a reverse proxy (nginx/Apache) for the webhook endpoint
4. Monitor webhook health and resubscribe if needed
5. Implement webhook event deduplication

## Additional Events

You can subscribe to other events by modifying the `subscribe_to_events` method:
- `channel.update` - Stream title/category changes
- `channel.follow` - New followers (requires moderator scope)
- `channel.subscribe` - New subscribers
- `channel.raid` - Incoming raids
- `stream.online` - Stream goes live
- `stream.offline` - Stream ends

Each event type may require different scopes and conditions.