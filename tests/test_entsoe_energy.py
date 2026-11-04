"""Volume-only SEPS + API prices, provenance, conservative alignment and recovery."""
import copy
import io
import json

import openpyxl
import pandas as pd
import pytest

from bri import artifacts, pipeline, settings
from bri.importers import entsoe_energy as energy, seps
from bri.timeutil import day_bounds
from tests.test_local_inbox import DAY, local_dirs, _capacity, _count, _report
from tests.conftest import FIXTURES, seps_xlsx


def volume_xlsx(day=DAY, mirrored=False, bad_mirror=False):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'aFRR'
    sheet.append([None, None, None, 'Up', 'Down'])
    sheet.append([None, None, None, 'Objem [MWh]', 'Objem [MWh]'])
    if mirrored:
        aggregate, mfrr = book.create_sheet('Spolu'), book.create_sheet('mFRR')
        aggregate.append([None] * 6 + ['12.3.E [MW]', None])
        mfrr.append([None] * 3 + ['Up', 'Up', 'Down', 'Down'])
    start, end = day_bounds(day)
    quarters = int((end - start) / pd.Timedelta(minutes=15))
    for index in range(quarters):
        prefix = [day.strftime('%d.%m.%Y') if index == 0 else None, index // 4 + 1, index % 4 + 1]
        sheet.append(prefix + [4.0, 2.0])
        if mirrored:
            mfrr.append(prefix + [1.0, 2.0, 3.0, 4.0])
            aggregate.append(prefix + ['Final', None, None, 8.0 if bad_mirror else 7.0, 9.0])
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def price_xml(day=DAY, process='A16', currency='EUR', category='A07', curve='A01', step='PT15M'):
    start, end = day_bounds(day)
    count = int((end - start) / energy.price_resolution(step))
    series = []
    for direction, price in (('A01', 120), ('A02', -30)):
        points = ''.join('<Point><position>{}</position><activation_Price.amount>{}</activation_Price.amount></Point>'.format(i, price)
                         for i in range(1, (2 if curve == 'A03' else count + 1)))
        series.append('''<TimeSeries><businessType>A96</businessType><flowDirection.direction>{direction}</flowDirection.direction>
          <standard_MarketProduct.marketProductType>A01</standard_MarketProduct.marketProductType>
          <currency_Unit.name>{currency}</currency_Unit.name><price_Measure_Unit.name>MWH</price_Measure_Unit.name>
          <imbalance_Price.category>{category}</imbalance_Price.category><curveType>{curve}</curveType>
          <Period><timeInterval><start>{start}</start><end>{end}</end></timeInterval><resolution>{step}</resolution>{points}</Period>
        </TimeSeries>'''.format(direction=direction, currency=currency, category=category, curve=curve,
                              start=start.isoformat(), end=end.isoformat(), step=step, points=points))
    return ('''<Balancing_MarketDocument xmlns="urn:entsoe.eu:wgedi:errpdocument:6:0"><mRID>test-price</mRID>
       <revisionNumber>1</revisionNumber><type>A84</type><process.processType>{}</process.processType>
       <createdDateTime>2026-10-01T12:00:00Z</createdDateTime>{}</Balancing_MarketDocument>'''.format(process, ''.join(series))).encode()


@pytest.mark.parametrize('curve,step', [('A01', 'PT15M'), ('A03', 'PT15M'), ('A01', 'PT1M')])
def test_native_prices_align_to_local_day(curve, step):
    parsed = seps.inspect_local_xlsx(volume_xlsx())
    prices, profile = energy.select_prices(energy.parse_prices(price_xml(curve=curve, step=step)), parsed['volumes'])
    assert len(prices) == 192
    assert prices[(parsed['volumes'][0][0], 'Up')] == 120
    assert prices[(parsed['volumes'][0][0], 'Down')] == -30
    assert profile['process'] == 'A16'


@pytest.mark.parametrize('kwargs', [{'currency': 'USD'}, {'category': 'A08'}, {'process': 'A67'}, {'curve': 'A99'}])
def test_wrong_price_series_are_not_substituted(kwargs):
    with pytest.raises(energy.PricesUnavailable):
        energy.parse_prices(price_xml(**kwargs))


def test_missing_active_price_and_ambiguous_profiles_are_pending():
    intervals = seps.inspect_local_xlsx(volume_xlsx())['volumes']
    records = energy.parse_prices(price_xml())
    with pytest.raises(energy.PricesUnavailable):
        energy.select_prices(records[1:], intervals)
    other = copy.deepcopy(records)
    for row in other:
        row['profile'] = ('A16', 'A06') + row['profile'][2:]
        row['price'] += 10
    with pytest.raises(energy.PricesUnavailable, match='Several different'):
        energy.select_prices(records + other, intervals)


def test_revisions_and_subquarter_variation_are_not_silently_averaged():
    intervals = seps.inspect_local_xlsx(volume_xlsx())['volumes']
    records = energy.parse_prices(price_xml())
    corrected = copy.deepcopy(records[0])
    corrected.update(revision=2, price=130)
    prices, _ = energy.select_prices(records + [corrected], intervals)
    assert prices[(intervals[0][0], 'Up')] == 130
    conflicting = copy.deepcopy(corrected)
    conflicting['price'] = 140
    with pytest.raises(energy.PricesUnavailable, match='Conflicting'):
        energy.select_prices(records + [corrected, conflicting], intervals)
    minute_rows = energy.parse_prices(price_xml(step='PT1M'))
    minute_rows[0]['price'] = 1000
    with pytest.raises(energy.PricesUnavailable, match='sub-interval'):
        energy.select_prices(minute_rows, intervals)


@pytest.mark.parametrize('day,quarters', [(pd.Timestamp('2026-03-29').date(), 92), (pd.Timestamp('2026-10-25').date(), 100)])
def test_dst_days_are_read_in_real_time(day, quarters):
    parsed = seps.inspect_local_xlsx(volume_xlsx(day))
    assert parsed['source_rows'] == quarters
    prices, _ = energy.select_prices(energy.parse_prices(price_xml(day)), parsed['volumes'])
    assert len(prices) == 2 * quarters


def test_power_unit_conversion_requires_verified_full_workbook_identity():
    parsed = seps.inspect_local_xlsx(volume_xlsx(mirrored=True))
    assert parsed['volumes'][0][2:] == (1.0, 0.5)
    assert parsed['volume_metadata']['declared_unit'] == 'MWh'
    assert parsed['volume_metadata']['interpreted_source_unit'] == 'MW'
    assert seps.inspect_local_xlsx(volume_xlsx())['volumes'][0][2:] == (4.0, 2.0)
    with pytest.raises(seps.FormatError, match='cross-check failed'):
        seps.inspect_local_xlsx(volume_xlsx(mirrored=True, bad_mirror=True))


def _automatic_run():
    return pipeline.main(['--source', 'local', '--delete-after-success', '--trigger', 'test'])


def test_volume_file_downloads_prices_capacity_publishes_and_cleans(conn, local_dirs, monkeypatch):
    inbox, reports = local_dirs
    calls = []
    def download(day):
        calls.append(str(day))
        return price_xml(), {'documentType': 'A84', 'processType': 'A16'}
    monkeypatch.setattr(energy, '_download', download)
    monkeypatch.setattr(settings, 'ENTSOE_API_KEY', 'private-test-token')
    monkeypatch.setattr(pipeline.entsoe, 'import_range', lambda connection, first, last: _capacity(connection, DAY))
    monkeypatch.setattr(pipeline, 'fetch_okte', lambda connection, days, recent, messages: set())
    content = volume_xlsx(mirrored=True)
    (inbox / 'volumes.xlsx').write_bytes(content)
    assert _automatic_run() == 0
    entry = _report(reports)['files'][0]
    assert calls == [str(DAY)]
    assert entry['status'] == 'completed' and entry['published_results'] == 4
    assert entry['input_deleted'] and entry['raw_cleanup'] == 'purged'
    assert entry['additional_raw_artifacts'][0]['cleanup'] == 'purged'
    assert entry['artifact_sha256'] != entry['sha256']
    assert _count(conn, 'afrr_energy') == 96
    with conn.cursor() as cur:
        cur.execute('SELECT DISTINCT up_volume_mwh,up_price_eur_mwh,down_volume_mwh,down_price_eur_mwh FROM afrr_energy_current')
        assert [[float(v) for v in r] for r in cur.fetchall()] == [[1.0, 120.0, 0.5, -30.0]]
    conn.commit()
    api_artifact = entry['price_documents'][0]['artifact_id']
    assert artifacts.read_artifact(conn, api_artifact) == price_xml()
    # Re-upload after local originals were purged: cached API prices, no duplicate batch/result.
    batches, runs = _count(conn, 'import_batch'), _count(conn, 'calc_run')
    (inbox / 'same.xlsx').write_bytes(content)
    assert _automatic_run() == 0
    assert calls == [str(DAY)]
    assert _count(conn, 'import_batch') == batches and _count(conn, 'calc_run') == runs


def test_api_outage_retains_file_then_resumes(conn, local_dirs, monkeypatch):
    inbox, reports = local_dirs
    (inbox / 'volumes.xlsx').write_bytes(volume_xlsx())
    def unavailable(day):
        raise energy.PricesUnavailable('ENTSO-E temporarily unavailable')
    monkeypatch.setattr(energy, '_download', unavailable)
    monkeypatch.setattr(pipeline, 'fetch_okte', lambda *args: set())
    monkeypatch.setattr(pipeline, 'fetch_entsoe', lambda *args: set())
    assert _automatic_run() == 1
    assert _report(reports)['files'][0]['status'] == 'waiting_api'
    assert _count(conn, 'afrr_energy') == 0
    assert len(list((inbox / '.processing').glob('*.xlsx'))) == 1
    assert not list((inbox / 'errors').glob('*.xlsx'))
    _capacity(conn)
    monkeypatch.setattr(energy, '_download', lambda day: (price_xml(), {}))
    assert _automatic_run() == 0
    assert _report(reports)['files'][0]['status'] == 'completed'


def test_download_retries_and_never_discloses_token(monkeypatch):
    from urllib.error import URLError
    monkeypatch.setattr(settings, 'ENTSOE_API_KEY', 'very-private-test-token')
    calls = []
    def fail(url, timeout):
        calls.append(url)
        raise URLError(url)
    monkeypatch.setattr(energy, 'urlopen', fail)
    monkeypatch.setattr(energy.time, 'sleep', lambda _: None)
    with pytest.raises(energy.PricesUnavailable) as error:
        energy._download(str(DAY))
    assert len(calls) == 3
    assert 'very-private-test-token' not in str(error.value)


def test_real_archived_a84_matches_seps_prices_and_point_category():
    from pathlib import Path
    records = energy.parse_prices((FIXTURES / 'entsoe_afrr_energy_prices_2026_05_23.xml').read_bytes())
    assert len(records) == 192
    assert {r['profile'][1] for r in records} == {'A06'}
    assert {r['profile'][3] for r in records} == {'A04'}
    assert records[0]['price'] == 94.3605
    # The archived API request covered a UTC day; the SEPS CSV covers a Slovak
    # local day. Compare their actual overlapping timestamps, not row numbers.
    csv = FIXTURES / 'seps_damas_afrr_energy_2026-05-23.csv'
    frame = pd.read_csv(str(csv))
    lookup = {(r['start'], r['direction']): r['price'] for r in records}
    matched = 0
    for row in frame.itertuples():
        start = pd.Timestamp(row.timestamp_local).tz_localize(settings.TZ).tz_convert('UTC')
        for direction, price in [('Up', row.up_price_eur_mwh), ('Down', row.down_price_eur_mwh)]:
            if (start, direction) in lookup:
                assert lookup[(start, direction)] == pytest.approx(float(price), abs=1e-9)
                matched += 1
    assert matched == 176


def test_point_price_categories_are_never_mixed_or_treated_as_cbmp():
    payload = price_xml(category='A06').decode()
    payload = payload.replace('<imbalance_Price.category>A06</imbalance_Price.category>', '')
    payload = payload.replace('</activation_Price.amount>', '</activation_Price.amount><imbalance_Price.category>A08</imbalance_Price.category>')
    with pytest.raises(energy.PricesUnavailable):
        energy.parse_prices(payload.encode())
    payload = payload.replace('>A08<', '>A06<')
    records = energy.parse_prices(payload.encode())
    records[0]['profile'] = ('A16','A07') + records[0]['profile'][2:]
    with pytest.raises(energy.PricesUnavailable):
        energy.select_prices(records, seps.inspect_local_xlsx(volume_xlsx())['volumes'])


def _nine_column_xlsx(conflict=False):
    book = openpyxl.load_workbook(io.BytesIO(seps_xlsx(DAY)))
    ws = book['aFRR']
    ws.insert_cols(6)
    ws.insert_cols(9)
    headers = [
        [''] * 9,
        [None,None,None,'Generation',None,None,None,None,None],
        [None,None,None,'Up',None,None,'Down',None,None],
        [None,None,None,'Objem [MWh]','Osobitny','Standardny','Objem [MWh]','Osobitny','Standardny'],
        [None,None,None,None,'Cena [EUR]', 'Cena [EUR]',None,'Cena [EUR]','Cena [EUR]'],
    ]
    for i, values in enumerate(headers, start=1):
        for j,value in enumerate(values,start=1):ws.cell(i,j,value)
    if conflict:ws.cell(6,6,200.0)
    out=io.BytesIO();book.save(out);return out.getvalue()


def test_nine_column_legacy_export_uses_down_volume_header():
    rows=seps.parse_xlsx(_nine_column_xlsx())
    assert len(rows)==96
    assert rows[0][2:] == (1.0,100.0,0.0,50.0)
    assert rows[1][2:] == (0.0,100.0,1.0,50.0)


def test_two_different_product_prices_need_product_volumes():
    with pytest.raises(seps.FormatError, match='Several different SEPS prices'):
        seps.parse_xlsx(_nine_column_xlsx(conflict=True))
