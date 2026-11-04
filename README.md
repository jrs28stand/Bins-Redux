# Bins-Redux

Sends a phone alert the evening before a Basingstoke and Deane collection. It uses only Python's standard library and has no third-party packages.

- **Morning and evening runs:** scheduled for 07:38 and 18:45 in winter, and 08:38 and 19:45 in summer. Each alerts you only if something is due tomorrow, so you normally get two reminders the day before.
- **Late runs:** GitHub can start scheduled runs hours late. A run between midnight and 05:00 UK time counts as the previous evening's, so it reports today's bins.
- **Manual run:** it prints the dates found and sends a test alert.
- **Problem alert:** a high-priority alert if the site is unreachable or blocked, or if the dates look wrong. This repeats on each scheduled run until fixed.

## Setup

1. Install the ntfy app and subscribe to a long, random topic name on the default ntfy.sh server.
2. Find your UPRN at https://uprn.uk/ or https://www.findmyaddress.co.uk/search.
3. Keep `bins.yml` at `.github/workflows/bins.yml`, on the default branch so GitHub schedules it.
4. Under Settings > Secrets and variables > Actions, add repository secrets named `BINS_UPRN` and `NTFY_TOPIC`.
5. Under Actions > Bin reminder, choose **Run workflow**. Compare the dates in the log with the council's website, and check the test alert arrives.

## If it stops working

- **HTTP 403 or a connection error:** the council may be blocking GitHub's servers. Run `bins.py` on a home computer or Raspberry Pi with cron and the same two settings as environment variables.
- **No collection dates found:** the page has probably changed, so the parser in `bins.py` needs updating.
- **Alerts stop with no error:** GitHub pauses scheduled workflows in public repositories after 60 days without repository activity. Re-enable it under the Actions tab.
- **Failed runs:** GitHub normally emails you when a workflow fails. This also covers ntfy itself being unavailable.

## Privacy

Your UPRN goes only to the council, in a cookie. ntfy receives the bin types and dates. Anyone who knows your topic name could read or send alerts on it, so treat the name like a password.

This repository is public, so never commit your UPRN or topic name: keep them only in the two secrets. Workflow logs are public too. They can show bin types and dates, but GitHub masks the secret values.

## Tests

`python3 -m unittest -v test_bins` runs against a sample page based on the open-source scraper's section names, not a capture of the real page.

The approach comes from the MIT-licensed BasingstokeCouncil scraper in https://github.com/robbrad/UKBinCollectionData.
