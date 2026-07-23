import httpx
import sys

BASE_URL = "http://127.0.0.1:8000"

def run_verification():
    print("--- STARTING LIVE API VERIFICATION ---")
    
    # 1. Create a timeless client entity
    res = httpx.post(f"{BASE_URL}/entities", json={
        "name": "Test Fixtures Pvt Ltd",
        "materiality_threshold": 15000
    })
    assert res.status_code == 201, f"Failed to create entity: {res.text}"
    entity_id = res.json()["id"]
    print(f"Created entity with ID: {entity_id}")
    
    # 2. Upload period 1 XML
    print("Uploading Period 1 XML...")
    with open("../sample_data/test_fixture_with_exceptions.xml", "rb") as f:
        res = httpx.post(
            f"{BASE_URL}/entities/{entity_id}/upload",
            files={"file": ("test_fixture_with_exceptions.xml", f, "text/xml")}
        )
    assert res.status_code == 200, f"Period 1 upload failed: {res.text}"
    print("Period 1 uploaded successfully.")
    
    # 3. Upload period 2 XML
    print("Uploading Period 2 XML...")
    with open("../sample_data/test_fixture_period2_continuity.xml", "rb") as f:
        res = httpx.post(
            f"{BASE_URL}/entities/{entity_id}/upload",
            files={"file": ("test_fixture_period2_continuity.xml", f, "text/xml")}
        )
    assert res.status_code == 200, f"Period 2 upload failed: {res.text}"
    print("Period 2 uploaded successfully.")
    
    # 4. Fetch distinct periods from the entity
    res = httpx.get(f"{BASE_URL}/entities/{entity_id}/periods")
    assert res.status_code == 200, f"Failed to load periods: {res.text}"
    periods = res.json()
    import json
    print("Distinct periods loaded (JSON):\n" + json.dumps(periods, indent=2))
    assert len(periods) == 2, f"Expected 2 periods, found: {len(periods)}"
    
    # Sort periods by start date
    periods_sorted = sorted(periods, key=lambda x: x["period_start"])
    p1 = periods_sorted[0]
    p2 = periods_sorted[1]
    
    assert p1["period_start"] == "2025-04-01"
    assert p1["period_end"] == "2026-03-31"
    assert p2["period_start"] == "2026-04-01"
    assert p2["period_end"] == "2027-03-31"
    print("Period start/end dates verified correctly.")
    
    # 5. Run Scrutiny Pass for Period 2 (2026-04-01 to 2027-03-31)
    print("Running scrutiny for Period 2 (FY 2026-27)...")
    res = httpx.post(
        f"{BASE_URL}/entities/{entity_id}/scrutiny-run",
        params={
            "period_start": p2["period_start"],
            "period_end": p2["period_end"]
        }
    )
    assert res.status_code == 200, f"Scrutiny run failed: {res.text}"
    summary = res.json()
    print("Scrutiny run summary:", summary)
    
    # 6. Fetch exceptions for Period 2
    res = httpx.get(
        f"{BASE_URL}/entities/{entity_id}/exceptions",
        params={
            "period_start": p2["period_start"],
            "period_end": p2["period_end"]
        }
    )
    assert res.status_code == 200, f"Failed to get exceptions: {res.text}"
    exceptions = res.json()
    print("Exceptions loaded (JSON):\n" + json.dumps(exceptions, indent=2))
    for exc in exceptions:
        print(f" - [{exc['rule_name']}] {exc['ledger_account_name']}: {exc['message']}")
        
    # Verify exceptions under materiality threshold of 15000:
    # 1. Verma Traders (debit balance of 25000 > 15000) -> normal_balance_check
    # 2. Verma Traders (opening balance gap 20000 > 15000) -> opening_balance_continuity
    # Rahul Enterprises (3000 gap is below 15000, so filtered out of opening_balance_continuity)
    # Rahul Enterprises (normal balance check credit balance of 1000 is below 15000, so filtered out of normal_balance_check)
    # Petty Cash Variance (normal balance check debit balance of 12000 is below 15000, so filtered out)
    # Office Rent, Sales, Purchase (P&L accounts correctly reset to 0, no continuity exceptions)
    
    exc_rules = {exc["rule_name"]: exc for exc in exceptions}
    exc_accounts = {exc["ledger_account_name"] for exc in exceptions}
    
    assert len(exceptions) == 2, f"Expected exactly 2 exceptions, found {len(exceptions)}"
    assert "Verma Traders" in exc_accounts, "Verma Traders exception missing!"
    assert "Rahul Enterprises" not in exc_accounts, "Rahul Enterprises exception was not filtered out!"
    assert "normal_balance_check" in exc_rules
    assert "opening_balance_continuity" in exc_rules
    
    print("\n--- ALL API VERIFICATION TESTS PASSED SUCCESSFULLY! ---")

if __name__ == "__main__":
    try:
        run_verification()
    except Exception as e:
        print(f"Error during verification: {e}")
        sys.exit(1)
