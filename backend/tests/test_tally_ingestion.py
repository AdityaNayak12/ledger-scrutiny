import os
from decimal import Decimal
from datetime import date
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Entity, ImportBatch, JournalEntry, JournalLine, LedgerAccount, Organization, Transaction, TrialBalanceSnapshot
from app.ingestion.batches import stage_import_batch
from app.ingestion.tally_http import TallyConnectorError
from app.ingestion.tally_parser import parse_tally_amount, parse_tally_xml
from app.ingestion.tally_normalizer import normalize_tally_data


def test_tally_ingestion_end_to_end():
    # 1. Read the sample XML file
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")
    with open(xml_path, "rb") as f:
        xml_content = f.read()

    # 2. Parse the XML file
    parsed_data = parse_tally_xml(xml_content)
    assert parsed_data["entity"]["name"] == "Acme Audited Corp"
    assert parsed_data["entity"]["financial_year_start"] == date(2025, 4, 1)
    assert parsed_data["entity"]["financial_year_end"] == date(2026, 3, 31)
    assert len(parsed_data["ledgers"]) == 5
    assert len(parsed_data["vouchers"]) == 2

    # 3. Create clean in-memory SQLite DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 4. Normalize and persist parsed data
        materiality = Decimal("15000.00")
        entity = normalize_tally_data(parsed_data, session, materiality_threshold=materiality)
        session.commit()

        # 5. Assertions on Entity
        assert entity.id is not None
        assert entity.name == "Acme Audited Corp"
        assert entity.materiality_threshold == materiality

        # 6. Assertions on Ledger Accounts and normal balances
        accounts = session.query(LedgerAccount).filter_by(entity_id=entity.id).all()
        assert len(accounts) == 5

        acc_map = {acc.name: acc for acc in accounts}
        assert acc_map["Cash-in-hand"].normal_balance == "debit"
        assert acc_map["Cash-in-hand"].group_name == "Cash-in-hand"

        assert acc_map["Owner Capital"].normal_balance == "credit"
        assert acc_map["Owner Capital"].group_name == "Capital Account"

        assert acc_map["Machinery"].normal_balance == "debit"
        assert acc_map["Machinery"].group_name == "Fixed Assets"

        assert acc_map["Sales Account"].normal_balance == "credit"
        assert acc_map["Sales Account"].group_name == "Sales Accounts"

        assert acc_map["ACME Debtors"].normal_balance == "debit"
        assert acc_map["ACME Debtors"].group_name == "Sundry Debtors"

        # 7. Assertions on Transactions
        transactions = session.query(Transaction).filter_by(entity_id=entity.id).all()
        assert len(transactions) == 2

        txn_map = {txn.source_voucher_id: txn for txn in transactions}

        # Voucher 1: Receipt (debit Cash-in-hand, credit Owner Capital)
        vch1 = txn_map["VCH-0001"]
        assert vch1.voucher_type == "Receipt"
        assert vch1.amount == Decimal("50000.00")
        assert vch1.debit_account.name == "Cash-in-hand"
        assert vch1.credit_account.name == "Owner Capital"
        assert vch1.narration == "Capital introduced by owner"
        assert vch1.date == date(2025, 4, 1)

        # Voucher 2: Sales (debit ACME Debtors, credit Sales Account)
        vch2 = txn_map["VCH-0002"]
        assert vch2.voucher_type == "Sales"
        assert vch2.amount == Decimal("20000.00")
        assert vch2.debit_account.name == "ACME Debtors"
        assert vch2.credit_account.name == "Sales Account"
        assert vch2.narration == "Service sales to ACME Debtors"
        assert vch2.date == date(2025, 5, 12)

        # 8. Assertions on Trial Balance Snapshots
        snapshots = session.query(TrialBalanceSnapshot).filter_by(entity_id=entity.id).all()
        assert len(snapshots) == 5

        snap_map = {snap.ledger_account.name: snap for snap in snapshots}

        # Cash-in-hand: opening=10000 (Dr), debit=50000, credit=0, closing=60000 (Dr)
        cash_snap = snap_map["Cash-in-hand"]
        assert cash_snap.opening_balance == Decimal("10000.00")
        assert cash_snap.total_debits == Decimal("50000.00")
        assert cash_snap.total_credits == Decimal("0.00")
        assert cash_snap.closing_balance == Decimal("60000.00")
        assert cash_snap.period_start == date(2025, 4, 1)
        assert cash_snap.period_end == date(2026, 3, 31)

        # Owner Capital: opening=0, debit=0, credit=50000, closing=-50000 (Cr)
        capital_snap = snap_map["Owner Capital"]
        assert capital_snap.opening_balance == Decimal("0.00")
        assert capital_snap.total_debits == Decimal("0.00")
        assert capital_snap.total_credits == Decimal("50000.00")
        assert capital_snap.closing_balance == Decimal("-50000.00")

        # Machinery: opening=150000 (Dr), debit=0, credit=0, closing=150000 (Dr)
        machinery_snap = snap_map["Machinery"]
        assert machinery_snap.opening_balance == Decimal("150000.00")
        assert machinery_snap.total_debits == Decimal("0.00")
        assert machinery_snap.total_credits == Decimal("0.00")
        assert machinery_snap.closing_balance == Decimal("150000.00")

        # Sales Account: opening=0, debit=0, credit=20000, closing=-20000 (Cr)
        sales_snap = snap_map["Sales Account"]
        assert sales_snap.opening_balance == Decimal("0.00")
        assert sales_snap.total_debits == Decimal("0.00")
        assert sales_snap.total_credits == Decimal("20000.00")
        assert sales_snap.closing_balance == Decimal("-20000.00")

    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_tally_ingestion_edge_cases():
    # 1. Test missing CLOSINGBALANCE
    xml_missing_closing = b"""<ENVELOPE>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>Trial Balance</REPORTNAME>
            <STATICVARIABLES>
              <SVFROMDATE>20250401</SVFROMDATE>
              <SVTODATE>20260331</SVTODATE>
              <SVCOMPANYNAME>Test Corp</SVCOMPANYNAME>
            </STATICVARIABLES>
          </REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE>
              <LEDGER NAME="Bad Account">
                <PARENT>Capital Account</PARENT>
                <OPENINGBALANCE>100.00</OPENINGBALANCE>
                <!-- Missing CLOSINGBALANCE entirely -->
              </LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""

    import pytest
    with pytest.raises(ValueError) as excinfo:
        parse_tally_xml(xml_missing_closing)
    assert "missing CLOSINGBALANCE" in str(excinfo.value)

    # 2. Test unbalanced voucher
    xml_unbalanced_voucher = b"""<ENVELOPE>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <STATICVARIABLES>
              <SVFROMDATE>20250401</SVFROMDATE>
              <SVTODATE>20260331</SVTODATE>
              <SVCOMPANYNAME>Test Corp</SVCOMPANYNAME>
            </STATICVARIABLES>
          </REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE>
              <LEDGER NAME="Share Capital">
                <PARENT>Capital Account</PARENT>
                <OPENINGBALANCE>0.00</OPENINGBALANCE>
                <CLOSINGBALANCE>0.00</CLOSINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <VOUCHER VCHTYPE="Journal" DATE="20250615">
                <VOUCHERNUMBER>ERR-999</VOUCHERNUMBER>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Share Capital</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                  <AMOUNT>1000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Sales Account</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                  <AMOUNT>-400.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
              </VOUCHER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""

    with pytest.raises(ValueError) as excinfo:
        parse_tally_xml(xml_unbalanced_voucher)
    assert "is unbalanced" in str(excinfo.value)
    assert "ERR-999" in str(excinfo.value)
    assert "1000.00" in str(excinfo.value)
    assert "400.00" in str(excinfo.value)

    # 3. Test unmapped custom group
    xml_unmapped_group = b"""<ENVELOPE>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <STATICVARIABLES>
              <SVFROMDATE>20250401</SVFROMDATE>
              <SVTODATE>20260331</SVTODATE>
              <SVCOMPANYNAME>Test Corp</SVCOMPANYNAME>
            </STATICVARIABLES>
          </REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE>
              <LEDGER NAME="Custom Asset">
                <PARENT>NonExistentGroup</PARENT>
                <OPENINGBALANCE>0.00</OPENINGBALANCE>
                <CLOSINGBALANCE>0.00</CLOSINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""

    parsed = parse_tally_xml(xml_unmapped_group)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        with pytest.raises(TallyConnectorError) as excinfo:
            normalize_tally_data(parsed, session)
        assert "Unrecognized ledger account group" in str(excinfo.value)
        assert "NonExistentGroup" in str(excinfo.value)
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_malformed_xml_fixture():
    fixture_path = os.path.join(os.path.dirname(__file__), "../../sample_data/test_fixture_malformed.xml")
    with open(fixture_path, "rb") as f:
        xml_content = f.read()

    # The first error raised during parse_tally_xml will be missing closing balance
    import pytest
    with pytest.raises(ValueError) as excinfo:
        parse_tally_xml(xml_content)
    assert "missing CLOSINGBALANCE" in str(excinfo.value)
    assert "Account Missing Closing Balance" in str(excinfo.value)


def test_tally_ingestion_voucher_date_filtering():
    xml_content = b"""<ENVELOPE>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>All Ledger Entries</REPORTNAME>
          </REQUESTDESC>
          <REQUESTDATA>
            <COMPANY>
              <RENAME>Date Filter Corp</RENAME>
              <BOOKSFROM>20250401</BOOKSFROM>
              <BOOKSTO>20260331</BOOKSTO>
            </COMPANY>
            <TALLYMESSAGE>
              <LEDGER NAME="Cash-in-hand">
                <PARENT>Cash-in-hand</PARENT>
                <OPENINGBALANCE>10000.00</OPENINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <LEDGER NAME="Owner Capital">
                <PARENT>Capital Account</PARENT>
                <OPENINGBALANCE>0.00</OPENINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <VOUCHER VCHTYPE="Receipt">
                <DATE>20250501</DATE>
                <VOUCHERNUMBER>VCH-IN</VOUCHERNUMBER>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Cash-in-hand</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                  <AMOUNT>-50000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Owner Capital</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                  <AMOUNT>50000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
              </VOUCHER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <VOUCHER VCHTYPE="Receipt">
                <DATE>20260501</DATE>
                <VOUCHERNUMBER>VCH-OUT</VOUCHERNUMBER>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Cash-in-hand</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                  <AMOUNT>-20000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Owner Capital</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                  <AMOUNT>20000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
              </VOUCHER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>"""

    parsed_data = parse_tally_xml(xml_content)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        normalize_tally_data(
            parsed_data,
            session,
            clear_only_period=True,
            target_period_start=date(2025, 4, 1),
            target_period_end=date(2026, 3, 31)
        )
        session.commit()

        txns = session.query(Transaction).all()
        assert len(txns) == 1
        assert txns[0].source_voucher_id == "VCH-IN"

        snaps = session.query(TrialBalanceSnapshot).all()
        snap_map = {s.ledger_account.name: s for s in snaps}
        assert snap_map["Cash-in-hand"].total_debits == Decimal("50000.00")
        assert snap_map["Cash-in-hand"].closing_balance == Decimal("60000.00")
        assert snap_map["Owner Capital"].total_credits == Decimal("50000.00")
        assert snap_map["Owner Capital"].closing_balance == Decimal("-50000.00")

    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_tally_parser_preserves_multiline_voucher_ids_rows_and_signed_values():
    xml_content = b"""<ENVELOPE><BODY><EXPORTDATA><REQUESTDESC><REPORTNAME>All Ledger Entries</REPORTNAME></REQUESTDESC>
      <REQUESTDATA><COMPANY><RENAME>Multiline Corp</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
      <TALLYMESSAGE><LEDGER NAME="Bank"><GUID>BANK-1</GUID><PARENT>Bank Accounts</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>100</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>
      <TALLYMESSAGE><LEDGER NAME="Expense"><PARENT>Direct Expenses</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>60</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>
      <TALLYMESSAGE><LEDGER NAME="Tax"><PARENT>Duties &amp; Taxes</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>40</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>
      <TALLYMESSAGE><VOUCHER GUID="DOC-GUID" VCHTYPE="Journal"><DATE>20250615</DATE><VOUCHERNUMBER>DOC-1</VOUCHERNUMBER><NARRATION>Three line voucher</NARRATION>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>Bank</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-100</AMOUNT></ALLLEDGERENTRIES.LIST>
        <LEDGERENTRIES.LIST><LEDGERNAME>Expense</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>60</AMOUNT></LEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>Tax</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>40</AMOUNT></ALLLEDGERENTRIES.LIST>
      </VOUCHER></TALLYMESSAGE></REQUESTDATA></EXPORTDATA></BODY></ENVELOPE>"""

    parsed = parse_tally_xml(xml_content)
    voucher = parsed["vouchers"][0]
    assert voucher["source_voucher_id"] == "DOC-GUID"
    assert voucher["date"] == date(2025, 6, 15)
    assert voucher["voucher_type"] == "Journal"
    assert [entry["ledger_name"] for entry in voucher["entries"]] == ["Bank", "Expense", "Tax"]
    assert [entry["source_row_number"] for entry in voucher["entries"]] == [1, 2, 3]
    assert [(entry["type"], entry["amount"]) for entry in voucher["entries"]] == [
        ("debit", Decimal("100")), ("credit", Decimal("60")), ("credit", Decimal("40"))
    ]
    assert parsed["ledgers"][0]["external_id"] == "BANK-1"


def test_tally_amount_and_parser_fail_loudly_on_malformed_values():
    with pytest.raises(TallyConnectorError, match="Invalid Tally amount"):
        parse_tally_amount("not-a-number")
    with pytest.raises(TallyConnectorError, match="Invalid Tally amount"):
        parse_tally_amount("")

    malformed = b"""<ENVELOPE><COMPANY><RENAME>Broken</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
      <LEDGER NAME="Cash"><PARENT>Cash-in-hand</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>0</CLOSINGBALANCE></LEDGER>
      <VOUCHER VCHTYPE="Journal"><DATE>20250401</DATE><VOUCHERNUMBER>BAD-1</VOUCHERNUMBER><ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>oops</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER></ENVELOPE>"""
    with pytest.raises(TallyConnectorError, match="Invalid Tally amount"):
        parse_tally_xml(malformed)


def test_tally_parser_rejects_unbalanced_voucher_with_connector_error():
    xml_content = b"""<ENVELOPE><COMPANY><RENAME>Broken</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
      <LEDGER NAME="Cash"><PARENT>Cash-in-hand</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>0</CLOSINGBALANCE></LEDGER>
      <VOUCHER VCHTYPE="Journal"><DATE>20250401</DATE><VOUCHERNUMBER>BAD-2</VOUCHERNUMBER>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-100</AMOUNT></ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>99</AMOUNT></ALLLEDGERENTRIES.LIST>
      </VOUCHER></ENVELOPE>"""
    with pytest.raises(TallyConnectorError, match="does not balance"):
        parse_tally_xml(xml_content)


def test_tally_normalizer_writes_canonical_multiline_rows_and_replays_without_duplicates():
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")
    contents = open(xml_path, "rb").read()
    parsed_data = parse_tally_xml(contents)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        organization = Organization(name="Canonical Tally Org")
        session.add(organization)
        session.flush()
        entity = Entity(organization_id=organization.id, name="Acme Audited Corp", materiality_threshold=Decimal("0"))
        session.add(entity)
        session.flush()
        batch = stage_import_batch(
            session,
            entity_id=entity.id,
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
            source="tally_xml",
            source_family="tally",
            original_filename="sample.xml",
            contents=contents,
            uploaded_by_user_id=None,
        )

        normalize_tally_data(parsed_data, session, entity_id=entity.id, import_batch_id=batch.id)
        session.commit()
        assert session.scalar(select(func.count()).select_from(JournalEntry)) == 2
        assert session.scalar(select(func.count()).select_from(JournalLine)) == 4
        assert session.scalar(select(func.count()).select_from(Transaction)) == 2
        lines = list(session.scalars(select(JournalLine).order_by(JournalLine.id)))
        assert [line.side for line in lines] == ["debit", "credit", "debit", "credit"]
        assert [line.amount for line in lines] == [
            Decimal("50000"), Decimal("-50000"), Decimal("20000"), Decimal("-20000")
        ]
        first_ids = [entry.source_document_id for entry in session.scalars(select(JournalEntry).order_by(JournalEntry.id))]
        first_line_count = session.scalar(select(func.count()).select_from(JournalLine))

        normalize_tally_data(parsed_data, session, entity_id=entity.id, import_batch_id=batch.id)
        session.commit()
        assert [entry.source_document_id for entry in session.scalars(select(JournalEntry).order_by(JournalEntry.id))] == first_ids
        assert session.scalar(select(func.count()).select_from(JournalLine)) == first_line_count
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_tally_normalizer_maps_missing_entity_data_to_connector_error():
    with pytest.raises(TallyConnectorError, match="entity section"):
        normalize_tally_data({}, None)


def test_tally_parser_preserves_group_hierarchy():
    xml_content = b"""<ENVELOPE><COMPANY><RENAME>Hierarchy Corp</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
      <GROUP NAME="Assets" GUID="GROUP-ASSETS"><PARENT>Primary</PARENT></GROUP>
      <GROUP NAME="Bank Accounts" GUID="GROUP-BANK"><PARENT>Assets</PARENT></GROUP>
      <LEDGER NAME="Bank"><PARENT>Bank Accounts</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>0</CLOSINGBALANCE></LEDGER>
    </ENVELOPE>"""

    parsed = parse_tally_xml(xml_content)
    assert parsed["groups"] == [
        {"name": "Assets", "parent_name": "Primary", "external_id": "GROUP-ASSETS"},
        {"name": "Bank Accounts", "parent_name": "Assets", "external_id": "GROUP-BANK"},
    ]
    assert parsed["ledgers"][0]["group_hierarchy"] == ["Bank Accounts", "Assets", "Primary"]


def test_tally_normalizer_rejects_unknown_voucher_ledger_before_writing_accounts():
    parsed = {
        "entity": {"name": "Atomic Corp", "financial_year_start": date(2025, 4, 1), "financial_year_end": date(2026, 3, 31)},
        "ledgers": [{"name": "Cash", "group_name": "Cash-in-hand", "opening_balance": Decimal("0"), "closing_balance": Decimal("0")}],
        "vouchers": [{"source_voucher_id": "DOC-1", "date": date(2025, 4, 1), "voucher_type": "Journal", "entries": [
            {"ledger_name": "Cash", "type": "debit", "amount": Decimal("10")},
            {"ledger_name": "Missing", "type": "credit", "amount": Decimal("10")},
        ]}],
    }
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        organization = Organization(name="Atomic Org")
        session.add(organization)
        session.flush()
        entity = Entity(organization_id=organization.id, name="Atomic Corp", materiality_threshold=Decimal("0"))
        session.add(entity)
        session.flush()
        with pytest.raises(ValueError, match="unknown ledger"):
            normalize_tally_data(parsed, session, entity_id=entity.id)
        assert session.scalar(select(func.count()).select_from(LedgerAccount)) == 0
        assert session.scalar(select(func.count()).select_from(Transaction)) == 0
    finally:
        session.close()
        Base.metadata.drop_all(engine)
