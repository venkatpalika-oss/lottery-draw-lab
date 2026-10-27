# Lottery Draw Lab

A browser app for analysing lottery draw history: number frequencies, a chi-square fairness test, hot/cold numbers and gaps, a walk-forward backtest of five pick strategies against pure chance, a ticket generator, and exact odds.

Supports Mark Six (6/49), UK Lotto (6/59), EuroMillions main (5/50), Powerball white (5/69) and custom games. It works offline once loaded and can be installed to a phone's home screen.

## Use
Open the site, go to **Draw history**, and paste or upload a CSV of past draws (oldest first, or tick "Newest draw is listed first"). See `data/sample-format.csv` for the format. Until you import data, the app runs on a clearly labelled simulated sample.

## Deploy (GitHub Pages)
1. Push this folder to the `main` branch of a GitHub repository.
2. In the repo: **Settings → Pages → Source: GitHub Actions**.
3. The included workflow publishes the site at `https://<user>.github.io/<repo>/`.

## Note
Lottery draws are independent random events. The backtest exists to show that no history-based strategy beats random picks; play for fun and within a fixed budget.
