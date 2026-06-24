//! Integration tests for the `Divkit` client.
//!
//! Spins up a local wiremock HTTP server serving a synthetic manifest and the
//! committed fixture parquet, then asserts end-to-end dividend retrieval.

use divkit::Divkit;
use tempfile::TempDir;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

/// SHA-256 (hex) of `tests/fixtures/dividends-2024.parquet` at commit time.
///
/// Regenerate with: `sha256sum crates/divkit/tests/fixtures/dividends-2024.parquet`
const FIXTURE_SHA256: &str =
    "d0fe742c4c6de9147ed28e8bb85f82949361dbe64851603f1e2385fa1342ddd9";

/// Load the committed parquet fixture as bytes.
fn fixture_bytes() -> Vec<u8> {
    let manifest_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    std::fs::read(manifest_dir.join("tests/fixtures/dividends-2024.parquet"))
        .expect("fixture parquet must exist — run `cargo test --test parquet_io make_fixture -- --ignored` first")
}

/// Build the manifest JSON body: lists the shard with its sha256 digest so the
/// fetcher's verification path is exercised.
fn manifest_body() -> String {
    format!(
        r#"{{"dividends-2024.parquet": "sha256:{FIXTURE_SHA256}"}}"#
    )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

/// Known-ticker path: KO has 4×$0.485 = $1.94 trailing annual dividend.
#[tokio::test]
async fn annual_dividend_known_ticker() {
    let server = MockServer::start().await;
    let parquet = fixture_bytes();

    // Serve manifest.json
    Mock::given(method("GET"))
        .and(path("/manifest.json"))
        .respond_with(ResponseTemplate::new(200).set_body_string(manifest_body()))
        .expect(1..)
        .mount(&server)
        .await;

    // Serve the parquet shard
    Mock::given(method("GET"))
        .and(path("/dividends-2024.parquet"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(parquet))
        .expect(1..)
        .mount(&server)
        .await;

    let cache_dir = TempDir::new().unwrap();
    let client = Divkit::new()
        .with_base_url(server.uri())
        .with_cache_dir(cache_dir.path().to_path_buf())
        .with_mirror_url(None); // disable CDN fallback in tests

    let annual = client.annual_dividend("KO").await.unwrap();
    assert!(
        annual.is_some(),
        "KO is in the fixture — annual_dividend must return Some(_)"
    );
    let amount = annual.unwrap();
    assert!(
        (amount - 1.94).abs() < 1e-9,
        "expected ~1.94, got {amount}"
    );
}

/// Unknown-ticker path: ticker absent from all shards → Ok(None).
#[tokio::test]
async fn annual_dividend_unknown_ticker_returns_none() {
    let server = MockServer::start().await;
    let parquet = fixture_bytes();

    Mock::given(method("GET"))
        .and(path("/manifest.json"))
        .respond_with(ResponseTemplate::new(200).set_body_string(manifest_body()))
        .expect(1..)
        .mount(&server)
        .await;

    Mock::given(method("GET"))
        .and(path("/dividends-2024.parquet"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(parquet))
        .expect(1..)
        .mount(&server)
        .await;

    let cache_dir = TempDir::new().unwrap();
    let client = Divkit::new()
        .with_base_url(server.uri())
        .with_cache_dir(cache_dir.path().to_path_buf())
        .with_mirror_url(None);

    let annual = client.annual_dividend("NOPE").await.unwrap();
    assert_eq!(annual, None, "unknown ticker must return Ok(None)");
}

/// `dividends` returns one `DivEvent` per matching row.
#[tokio::test]
async fn dividends_for_known_ticker() {
    let server = MockServer::start().await;
    let parquet = fixture_bytes();

    Mock::given(method("GET"))
        .and(path("/manifest.json"))
        .respond_with(ResponseTemplate::new(200).set_body_string(manifest_body()))
        .expect(1..)
        .mount(&server)
        .await;

    Mock::given(method("GET"))
        .and(path("/dividends-2024.parquet"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(parquet))
        .expect(1..)
        .mount(&server)
        .await;

    let cache_dir = TempDir::new().unwrap();
    let client = Divkit::new()
        .with_base_url(server.uri())
        .with_cache_dir(cache_dir.path().to_path_buf())
        .with_mirror_url(None);

    let events = client.dividends("KO").await.unwrap();
    assert_eq!(events.len(), 4, "fixture has 4 KO rows");
    for ev in &events {
        assert!((ev.amount - 0.485).abs() < 1e-9);
    }
}

/// `dividend_snapshot` builds a `DividendSnapshot` with the correct ticker and CIK.
#[tokio::test]
async fn dividend_snapshot_for_known_ticker() {
    let server = MockServer::start().await;
    let parquet = fixture_bytes();

    Mock::given(method("GET"))
        .and(path("/manifest.json"))
        .respond_with(ResponseTemplate::new(200).set_body_string(manifest_body()))
        .expect(1..)
        .mount(&server)
        .await;

    Mock::given(method("GET"))
        .and(path("/dividends-2024.parquet"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(parquet))
        .expect(1..)
        .mount(&server)
        .await;

    let cache_dir = TempDir::new().unwrap();
    let client = Divkit::new()
        .with_base_url(server.uri())
        .with_cache_dir(cache_dir.path().to_path_buf())
        .with_mirror_url(None);

    let snap = client.dividend_snapshot("KO").await.unwrap();
    assert_eq!(snap.ticker, "KO");
    assert_eq!(snap.cik, 21344);
    assert_eq!(snap.history.len(), 4);
    assert!((snap.annual_amount() - 1.94).abs() < 1e-9);
}

/// Blocking wrapper works from synchronous context.
#[test]
fn annual_dividend_blocking_known_ticker() {
    // Build a tokio runtime to host the mock server, then call the blocking wrapper.
    let rt = tokio::runtime::Runtime::new().unwrap();

    let server = rt.block_on(async { MockServer::start().await });
    let parquet = fixture_bytes();

    rt.block_on(async {
        Mock::given(method("GET"))
            .and(path("/manifest.json"))
            .respond_with(ResponseTemplate::new(200).set_body_string(manifest_body()))
            .expect(1..)
            .mount(&server)
            .await;

        Mock::given(method("GET"))
            .and(path("/dividends-2024.parquet"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(parquet))
            .expect(1..)
            .mount(&server)
            .await;
    });

    let cache_dir = TempDir::new().unwrap();
    let client = Divkit::new()
        .with_base_url(server.uri())
        .with_cache_dir(cache_dir.path().to_path_buf())
        .with_mirror_url(None);

    let annual = client.annual_dividend_blocking("KO").unwrap();
    assert!(annual.is_some());
    assert!((annual.unwrap() - 1.94).abs() < 1e-9);
}
