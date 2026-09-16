"""Reproducible Census boundary download, maximum-area assignment and CSV validation."""
from pathlib import Path
import hashlib
import json
import zipfile
import geopandas as gpd
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
CSV = ROOT / 'places_zcta_selected_cleaned_wide.csv'
SLEEP = 'Short sleep duration among adults'
BASE = 'https://www2.census.gov/geo/tiger/GENZ2020/shp/'
FILES = {'zcta':'cb_2020_us_zcta520_500k.zip', 'state':'cb_2020_us_state_500k.zip'}


def assign_states(zctas, states):
    """Assign exactly one state by greatest polygon intersection area, not centroids.

    Equal-area EPSG:6933 covers all supplied US states/territories. Exact ties
    are resolved by state code. Unmatched polygons are not guessed.
    """
    z = zctas[['zcta','geometry']].to_crs(6933).copy()
    s = states[['state','state_name','geometry']].to_crs(6933).copy()
    z.geometry = z.geometry.make_valid()
    s.geometry = s.geometry.make_valid()
    overlaps = gpd.overlay(z, s, how='intersection', keep_geom_type=True)
    overlaps['area'] = overlaps.area
    areas = overlaps.groupby(['zcta','state','state_name'], as_index=False)['area'].sum()
    areas = areas[areas.area > 0]
    areas['parts'] = areas.groupby('zcta')['state'].transform('count')
    areas['overlap_share'] = areas.area / areas.groupby('zcta').area.transform('sum')
    return (areas.sort_values(['zcta','area','state'], ascending=[True,False,True])
            .drop_duplicates('zcta').drop(columns='area').reset_index(drop=True))


def download(filename):
    DATA.mkdir(exist_ok=True)
    dest = DATA / filename
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    temp = dest.with_suffix('.part')
    try:
        with requests.get(BASE + filename, stream=True, timeout=(15,180)) as response:
            response.raise_for_status()
            with temp.open('wb') as f:
                for chunk in response.iter_content(1024*1024):
                    f.write(chunk)
        if not zipfile.is_zipfile(temp):
            raise ValueError('Downloaded content is not a valid ZIP file.')
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)
    return dest


def prepare_boundaries():
    """Build once. Packaged display files allow startup without boundary downloads."""
    targets = [DATA/'states.parquet', DATA/'zctas.parquet', DATA/'zcta_state_mapping.csv']
    if all(p.exists() for p in targets):
        return
    states = gpd.read_file(download(FILES['state']))
    states = states.rename(columns={'STUSPS':'state','NAME':'state_name'})
    states = states[['state','state_name','geometry']].to_crs(4326)
    zctas = gpd.read_file(download(FILES['zcta']))
    zctas = zctas.rename(columns={'ZCTA5CE20':'zcta'})[['zcta','geometry']].to_crs(4326)
    zctas['zcta'] = zctas.zcta.astype(str).str.zfill(5)
    mapping = assign_states(zctas, states)
    mapping.to_csv(targets[2],index=False)
    # Simplification is ONLY for display, after assignment from full source geometry.
    for frame, path in [(states,targets[0]), (zctas,targets[1])]:
        projected = frame.to_crs(6933)
        projected.geometry = projected.geometry.simplify(100, preserve_topology=True)
        projected.to_crs(4326).to_parquet(path,index=False)
    (DATA/'boundary_sources.json').write_text(json.dumps({
        'sources':{k:BASE+v for k,v in FILES.items()},
        'assignment':'Maximum overlap in EPSG:6933; exact ties by state code',
        'display_simplification_m':100,
        'source_sha256':{v:hashlib.sha256((DATA/v).read_bytes()).hexdigest() for v in FILES.values()}
    },indent=2))


def load_dataset(path=CSV):
    raw = pd.read_csv(path,dtype={'LocationName':'string'})
    if not {'LocationName',SLEEP}.issubset(raw.columns):
        raise ValueError('CSV must contain LocationName and '+SLEEP)
    raw['zcta'] = raw.LocationName.str.strip().str.zfill(5)
    raw['rate'] = pd.to_numeric(raw[SLEEP], errors='coerce')
    valid = raw.zcta.str.fullmatch(r'\d{5}',na=False) & raw.rate.between(0,100)
    data = raw.loc[valid].copy()
    if data.zcta.duplicated().any():
        raise ValueError('Duplicate ZCTAs: use one year and one estimate type.')
    if data.empty:
        raise ValueError('No valid ZCTA percentages in this file.')
    mapping = pd.read_csv(DATA/'zcta_state_mapping.csv',dtype={'zcta':str})
    data = data.merge(mapping,on='zcta',how='left',validate='one_to_one')
    stats = data.dropna(subset=['state']).groupby(['state','state_name'],as_index=False).agg(
        rate=('rate','mean'), n=('zcta','size'))
    report = {'input_rows':len(raw),'valid_rows':len(data),'invalid_rows':int((~valid).sum()),
              'unmatched_rows':int(data.state.isna().sum()),
              'matched_states':len(stats),'national_mean':float(data.rate.mean()),
              'cross_boundary_rows':int((data.parts.fillna(0)>1).sum())}
    return data,stats,report


if __name__=='__main__':
    prepare_boundaries()
    data,stats,report=load_dataset()
    data.drop(columns='Geolocation',errors='ignore').to_csv(DATA/'mapped_sleep_data.csv',index=False)
    stats.to_csv(DATA/'state_summary.csv',index=False)
    (DATA/'validation_report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
