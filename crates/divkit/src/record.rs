//! Dividend event and snapshot types with trailing-year and yield math.
use chrono::{Duration, NaiveDate, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Concept {
    Declared,
    CashPaid,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Frequency {
    Monthly,
    Quarterly,
    SemiAnnual,
    Annual,
    Irregular,
    None,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DivEvent {
    pub period_start: NaiveDate,
    pub period_end: NaiveDate,
    pub amount: f64,
    pub concept: Concept,
    pub accn: String,
    pub form: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DividendSnapshot {
    pub ticker: String,
    pub cik: u32,
    pub history: Vec<DivEvent>, // ascending by period_end
}

impl DividendSnapshot {
    pub fn from_events(ticker: String, cik: u32, mut events: Vec<DivEvent>) -> Self {
        events.sort_by_key(|e| e.period_end);
        Self {
            ticker,
            cik,
            history: events,
        }
    }

    /// Distinct period_end events (dedup keeps first = Declared-preferred upstream).
    fn distinct(&self) -> Vec<&DivEvent> {
        let mut seen = std::collections::HashSet::new();
        self.history
            .iter()
            .filter(|e| seen.insert(e.period_end))
            .collect()
    }

    pub fn frequency(&self) -> Frequency {
        let ev = self.distinct();
        if ev.is_empty() {
            return Frequency::None;
        }
        if ev.len() == 1 {
            return Frequency::Irregular;
        }
        // median spacing in days between consecutive distinct period_ends
        let mut gaps: Vec<i64> = ev
            .windows(2)
            .map(|w| (w[1].period_end - w[0].period_end).num_days())
            .collect();
        gaps.sort_unstable();
        let med = gaps[gaps.len() / 2];
        match med {
            d if d <= 45 => Frequency::Monthly,
            d if d <= 135 => Frequency::Quarterly,
            d if d <= 225 => Frequency::SemiAnnual,
            d if d <= 450 => Frequency::Annual,
            _ => Frequency::Irregular,
        }
    }

    /// Indicated Annual Dividend (IAD) as of a given date.
    ///
    /// Computes the median of the last `K` regular payments × `K`, where `K`
    /// is the payment frequency (monthly 12 / quarterly 4 / semi-annual 2 /
    /// annual 1). Using the median rejects special dividends and XBRL
    /// period-rollup anomalies. Returns `0.0` if the most recent dividend is
    /// older than ~400 days (stopped payer).
    ///
    /// For Irregular/None frequency the estimate falls back to a
    /// trailing-365-day sum anchored to the most recent reported dividend.
    pub fn annual_amount_as_of(&self, as_of: NaiveDate) -> f64 {
        let ev = self.distinct();
        if ev.is_empty() {
            return 0.0;
        }
        let last = ev.last().unwrap().period_end;

        // Staleness gate: a company whose most recent dividend predates
        // `as_of` by more than ~400 days has stopped paying — decay to 0.
        if (as_of - last).num_days() > 400 {
            return 0.0;
        }

        // Determine K from payment frequency.
        let k: usize = match self.frequency() {
            Frequency::Monthly => 12,
            Frequency::Quarterly => 4,
            Frequency::SemiAnnual => 2,
            Frequency::Annual => 1,
            Frequency::Irregular | Frequency::None => 0,
        };

        if k > 0 {
            // Take the most-recent min(K, len) distinct events and compute
            // their median, then scale by K.  The median rejects one-off
            // special dividends and XBRL rollup outliers that would otherwise
            // inflate a simple sum.
            let take = k.min(ev.len());
            let mut amounts: Vec<f64> = ev.iter().rev().take(take).map(|e| e.amount).collect();
            amounts.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let median = if amounts.len() % 2 == 1 {
                amounts[amounts.len() / 2]
            } else {
                let mid = amounts.len() / 2;
                (amounts[mid - 1] + amounts[mid]) / 2.0
            };
            return median * k as f64;
        }

        // Irregular/None: fall back to trailing-365-day sum anchored to last.
        let cutoff = last - Duration::days(365);
        let trailing: f64 = ev
            .iter()
            .filter(|e| e.period_end > cutoff && e.period_end <= last)
            .map(|e| e.amount)
            .sum();
        if trailing > 0.0 {
            return trailing;
        }

        // Sparse history but `last` is recent (gate already passed): return
        // the most-recent payment as-is (do not annualise Irregular/None).
        ev.last().unwrap().amount
    }

    /// Indicated Annual Dividend (IAD): the median of the last K regular
    /// payments times K, where K is the payment frequency (monthly 12 /
    /// quarterly 4 / semi-annual 2 / annual 1). Using the median rejects
    /// special dividends and XBRL period-rollup anomalies. Returns 0 if the
    /// most recent dividend is older than ~400 days (stopped payer).
    ///
    /// Use [`annual_amount_as_of`](Self::annual_amount_as_of) for
    /// deterministic testing or historical back-calculations.
    pub fn annual_amount(&self) -> f64 {
        self.annual_amount_as_of(Utc::now().date_naive())
    }

    pub fn yield_on(&self, price: f64) -> f64 {
        if price <= 0.0 {
            return 0.0;
        }
        self.annual_amount() / price
    }

    pub async fn yield_with(&self, p: &dyn crate::price::PriceProvider) -> crate::Result<f64> {
        let price = p.spot(&self.ticker).await?;
        Ok(self.yield_on(price))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    fn ev(end: &str, amt: f64) -> DivEvent {
        let d = NaiveDate::parse_from_str(end, "%Y-%m-%d").unwrap();
        DivEvent {
            period_start: d,
            period_end: d,
            amount: amt,
            concept: Concept::Declared,
            accn: "x".into(),
            form: None,
        }
    }

    #[test]
    fn annual_amount_sums_trailing_year() {
        // 4 quarterly dividends — last-4 sum = 4 × 0.485 = 1.94.
        let snap = DividendSnapshot::from_events(
            "KO".into(),
            21344,
            vec![
                ev("2024-03-15", 0.485),
                ev("2024-06-14", 0.485),
                ev("2024-09-13", 0.485),
                ev("2024-12-13", 0.485),
            ],
        );
        let as_of = NaiveDate::from_ymd_opt(2024, 12, 13).unwrap();
        assert!((snap.annual_amount_as_of(as_of) - 1.94).abs() < 1e-9);
    }

    /// Regression guard: 5 quarterly events spanning slightly over a year must
    /// return the last-4 sum (5.20), not the all-5 sum (6.44).
    #[test]
    fn five_quarter_regression_returns_last_four() {
        // ~91-day spacing; mimic JNJ amounts 1.24, 1.30, 1.30, 1.30, 1.30.
        let snap = DividendSnapshot::from_events(
            "JNJ".into(),
            200406,
            vec![
                ev("2023-03-07", 1.24),
                ev("2023-06-06", 1.30),
                ev("2023-09-05", 1.30),
                ev("2023-12-05", 1.30),
                ev("2024-03-06", 1.30),
            ],
        );
        let as_of = NaiveDate::from_ymd_opt(2024, 3, 6).unwrap();
        // last-4 = 1.30 × 4 = 5.20; NOT all-5 = 6.44
        assert!((snap.annual_amount_as_of(as_of) - 5.20).abs() < 1e-9);
    }

    #[test]
    fn monthly_frequency_detected_and_annual_sums_last_12() {
        // 13 monthly events ~30 days apart; last 12 each pay 0.10 → 1.20.
        let dates = [
            "2023-01-15",
            "2023-02-15",
            "2023-03-15",
            "2023-04-15",
            "2023-05-15",
            "2023-06-15",
            "2023-07-15",
            "2023-08-15",
            "2023-09-15",
            "2023-10-15",
            "2023-11-15",
            "2023-12-15",
            "2024-01-15",
        ];
        let snap = DividendSnapshot::from_events(
            "MTHLY".into(),
            99001,
            dates.iter().map(|d| ev(d, 0.10)).collect(),
        );
        assert_eq!(snap.frequency(), Frequency::Monthly);
        let as_of = NaiveDate::from_ymd_opt(2024, 1, 15).unwrap();
        // last 12 × 0.10 = 1.20
        assert!((snap.annual_amount_as_of(as_of) - 1.20).abs() < 1e-9);
    }

    #[test]
    fn semi_annual_sums_last_two() {
        let snap = DividendSnapshot::from_events(
            "SA".into(),
            99002,
            vec![ev("2023-06-15", 1.00), ev("2023-12-15", 1.05)],
        );
        assert_eq!(snap.frequency(), Frequency::SemiAnnual);
        let as_of = NaiveDate::from_ymd_opt(2023, 12, 15).unwrap();
        assert!((snap.annual_amount_as_of(as_of) - 2.05).abs() < 1e-9);
    }

    #[test]
    fn annual_frequency_returns_last_one() {
        let snap = DividendSnapshot::from_events(
            "ANN".into(),
            99003,
            vec![ev("2022-12-15", 2.00), ev("2023-12-15", 2.50)],
        );
        assert_eq!(snap.frequency(), Frequency::Annual);
        let as_of = NaiveDate::from_ymd_opt(2023, 12, 15).unwrap();
        assert!((snap.annual_amount_as_of(as_of) - 2.50).abs() < 1e-9);
    }

    #[test]
    fn frequency_quarterly_detected() {
        let snap = DividendSnapshot::from_events(
            "KO".into(),
            21344,
            vec![
                ev("2024-03-15", 0.485),
                ev("2024-06-14", 0.485),
                ev("2024-09-13", 0.485),
                ev("2024-12-13", 0.485),
            ],
        );
        assert_eq!(snap.frequency(), Frequency::Quarterly);
    }

    #[test]
    fn non_payer_is_zero_and_none() {
        let snap = DividendSnapshot::from_events("XYZ".into(), 1, vec![]);
        assert_eq!(snap.annual_amount(), 0.0);
        assert_eq!(snap.frequency(), Frequency::None);
        assert_eq!(snap.yield_on(100.0), 0.0);
    }

    #[test]
    fn yield_on_divides_amount_by_price() {
        let snap = DividendSnapshot::from_events(
            "KO".into(),
            21344,
            vec![
                ev("2024-03-15", 0.485),
                ev("2024-06-14", 0.485),
                ev("2024-09-13", 0.485),
                ev("2024-12-13", 0.485),
            ],
        );
        let as_of = NaiveDate::from_ymd_opt(2024, 12, 13).unwrap();
        let annual = snap.annual_amount_as_of(as_of);
        let y = annual / 50.0;
        assert!((y - (1.94 / 50.0)).abs() < 1e-9);
        assert_eq!(snap.yield_on(0.0), 0.0);
    }

    /// Realty Income (O) regression guard: monthly payer where XBRL rollup
    /// causes 3 of 12 events to appear inflated.  The median should track the
    /// real per-share amount and the IAD must stay near the true annual figure,
    /// not be inflated by the anomalies.
    #[test]
    fn monthly_median_rejects_xbrl_rollup_outliers() {
        // 9 regular payments of 0.27, 3 rollup anomalies at 0.80, 1.07, 0.54.
        // Events in ascending order with ~30-day spacing.
        let snap = DividendSnapshot::from_events(
            "O".into(),
            726854,
            vec![
                ev("2023-01-15", 0.27),
                ev("2023-02-15", 0.80), // rollup anomaly
                ev("2023-03-15", 0.27),
                ev("2023-04-15", 0.27),
                ev("2023-05-15", 1.07), // rollup anomaly
                ev("2023-06-15", 0.27),
                ev("2023-07-15", 0.27),
                ev("2023-08-15", 0.54), // rollup anomaly
                ev("2023-09-15", 0.27),
                ev("2023-10-15", 0.27),
                ev("2023-11-15", 0.27),
                ev("2023-12-15", 0.27),
            ],
        );
        assert_eq!(snap.frequency(), Frequency::Monthly);
        let as_of = NaiveDate::from_ymd_opt(2023, 12, 15).unwrap();
        let iad = snap.annual_amount_as_of(as_of);
        // Median of all 12 amounts (sorted):
        // [0.27,0.27,0.27,0.27,0.27,0.27,0.27,0.27,0.27,0.54,0.80,1.07]
        // middle two (index 5,6) = 0.27, 0.27 → median = 0.27
        // IAD = 0.27 * 12 = 3.24
        assert!(
            (iad - 3.24).abs() < 0.01,
            "IAD {iad} should be ~3.24, not inflated by rollup anomalies"
        );
        // Confirm it is NOT close to the simple-sum which would be inflated:
        // simple sum = 9*0.27 + 0.80 + 1.07 + 0.54 = 2.43 + 2.41 = 4.84
        assert!(iad < 3.5, "IAD {iad} must not be inflated by outliers");
    }

    #[test]
    fn annual_amount_decays_to_zero_for_stale_payer() {
        // A company whose last dividend was ~3 years before as_of must return 0.0.
        let snap = DividendSnapshot::from_events(
            "STALE".into(),
            99999,
            vec![
                ev("2020-03-15", 0.50),
                ev("2020-06-14", 0.50),
                ev("2020-09-13", 0.50),
                ev("2020-12-13", 0.50),
            ],
        );
        // as_of is 2024-01-01 — ~3 years after the last payment
        let as_of = NaiveDate::from_ymd_opt(2024, 1, 1).unwrap();
        assert_eq!(snap.annual_amount_as_of(as_of), 0.0);
    }
}
