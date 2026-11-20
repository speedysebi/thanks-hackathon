# Hooking up a real phone number (Twilio)

For the demo, `/phone` runs the same code path with no account. To make a real number work:

1. **Expose the app publicly.** Deploy it (Render, Railway, Fly) or, for the weekend, run `ngrok http 8000` and copy the https URL.
2. **Get a number.** Sign up at twilio.com. A trial account gives free credit but can only text numbers you verify, which is fine for judging. Buy a 414 local number.
3. **Point it at the app.** Phone Numbers → your number → Messaging → "A message comes in" → Webhook → `https://YOUR-URL/sms` → HTTP POST.
4. **Turn on signature checking.** In `.env` set `TWILIO_AUTH_TOKEN` (Console → Account info) and `PUBLIC_URL=https://YOUR-URL`. After that, any request not signed by Twilio gets a 403.
5. Text the number `HELP`.

**Before real use in the US**, local numbers that send app texts need A2P 10DLC registration (brand + campaign). It has a small one-time fee and a monthly fee and takes a few days. Check current pricing on twilio.com. A toll-free number with toll-free verification is the other option.
