"""Run with: python -m streamlit run app.py"""
from pathlib import Path
import re
import geopandas as gpd
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium
from data_pipeline import CSV, DATA, prepare_boundaries, load_dataset
from maps import make_map

st.set_page_config(page_title='ZCTA Sleep Monitor',page_icon='🌙',layout='wide')
st.title('ZCTA Sleep Monitor')
st.write('Short sleep duration among adults · CDC PLACES · National, state and ZCTA comparisons')

@st.cache_data(show_spinner=False)
def get_data(path, modified):
    return load_dataset(Path(path))

@st.cache_data(show_spinner=False)
def get_geography():
    return gpd.read_parquet(DATA/'states.parquet'), gpd.read_parquet(DATA/'zctas.parquet')

@st.cache_data(show_spinner=False)
def state_shapes(state):
    states,zctas=get_geography()
    mapping=pd.read_csv(DATA/'zcta_state_mapping.csv',dtype={'zcta':str})
    ids=mapping.loc[mapping.state==state,'zcta']
    shape=zctas[zctas.zcta.isin(ids)].copy()
    # Clip display geometry to the assigned state; keep the full ZCTA estimate.
    boundary=states.loc[states.state==state,'geometry'].iloc[0]
    shape.geometry=shape.geometry.intersection(boundary)

    # Clipping can produce GeometryCollections containing polygons and lines.
    # Folium expects polygon coordinates, so retain all polygon parts recursively.
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    def polygon_parts(geom):
        if geom is None or geom.is_empty:
            return []
        if geom.geom_type == 'Polygon':
            return [geom]
        if hasattr(geom, 'geoms'):
            return [part for child in geom.geoms for part in polygon_parts(child)]
        return []

    def polygon_only(geom):
        parts = polygon_parts(geom)
        return unary_union(parts) if parts else Polygon()

    shape.geometry = shape.geometry.map(polygon_only)
    return shape[~shape.geometry.is_empty & shape.geometry.notna()].copy()

try:
    if not CSV.exists():
        st.error(f'Place your CSV beside app.py: {CSV.name}')
        st.stop()
    with st.spinner('Loading boundaries and ZCTA-to-state mapping… First build can take a few minutes.'):
        prepare_boundaries()
        data,summary,report=get_data(str(CSV),CSV.stat().st_mtime)
        states,zctas=get_geography()
except Exception as exc:
    st.error(f'Could not prepare the dashboard: {exc}')
    st.exception(exc)
    st.info('Check your CSV and internet connection. Boundary files can also be rebuilt with: python data_pipeline.py')
    st.stop()

# Map navigation is separate from the state/ZCTA filter selection.
for key,value in {'state':'US','zcta':'All','map_depth':'national','map_epoch':0}.items():
    if key not in st.session_state:
        st.session_state[key]=value

# Apply map-click navigation BEFORE instantiating widgets.
if '_pending_state' in st.session_state:
    st.session_state.state=st.session_state.pop('_pending_state')
    st.session_state.zcta='All'
    st.session_state.map_depth='state'
    st.session_state.map_epoch+=1


def state_changed():
    st.session_state.zcta='All'
    st.session_state.map_depth='national'
    st.session_state.map_epoch+=1


def zcta_changed():
    st.session_state.map_depth='national' if st.session_state.zcta=='All' else 'state'
    st.session_state.map_epoch+=1


def back_to_national():
    st.session_state.map_depth='national'
    st.session_state.zcta='All'
    st.session_state.map_epoch+=1


state_names=dict(zip(states.state,states.state_name))
options=['US']+summary.sort_values('state_name').state.tolist()
if st.session_state.state not in options:
    st.session_state.state='US'
    state_changed()
st.sidebar.header('Explore an area')
st.sidebar.selectbox('State / district',options,key='state',
    format_func=lambda x:'United States — All' if x=='US' else state_names[x],
    on_change=state_changed)
selected_state=st.session_state.state
subset=data if selected_state=='US' else data[data.state==selected_state]
zcta_options=['All']+sorted(subset.zcta.tolist()) if selected_state!='US' else ['All']
if st.session_state.zcta not in zcta_options:
    st.session_state.zcta='All'
st.sidebar.selectbox('ZCTA',zcta_options,key='zcta',disabled=selected_state=='US',on_change=zcta_changed)
selected_zcta=st.session_state.zcta
national_mean=float(data.rate.mean())
if selected_state=='US':
    title='United States'; value=national_mean; reference=None
elif selected_zcta=='All':
    title=state_names[selected_state]; value=float(subset.rate.mean()); reference=national_mean
else:
    title=f'ZCTA {selected_zcta}'; value=float(subset.loc[subset.zcta==selected_zcta,'rate'].iloc[0]); reference=float(subset.rate.mean())

c1,c2,c3=st.columns(3)
c1.metric(f'Short sleep · {title}',f'{value:.2f}%',
          None if reference is None else f'{value-reference:+.2f} percentage points',delta_color='inverse')
c2.metric('National ZCTA mean' if selected_zcta=='All' else f'{state_names[selected_state]} ZCTA mean',
          f'{national_mean if reference is None else reference:.2f}%')
c3.metric('Valid ZCTAs in scope',f'{len(subset):,}')
st.caption('Means give every valid ZCTA equal weight; these are not population-weighted or official state/national prevalence estimates. Differences are descriptive, not significance tests.')
if report['invalid_rows'] or report['unmatched_rows']:
    st.warning(f"Invalid rows excluded: {report['invalid_rows']:,}. ZCTAs without state mapping: {report['unmatched_rows']:,}; these remain in the national mean but cannot appear in state views.")

st.subheader('Geographic pattern')
st.caption('Hover to see values. Click a state on the national map to open its ZCTAs. Red outlines mark the selected area. Gray means no data.')

# Use a common color scale for all state maps and another common scale for all ZCTA maps.
def show_national_panel(frame,label,height=470,alaska=False,bounds=None):
    st.markdown(f'**{label}**')
    frame=frame.merge(summary[['state','rate','n']],on='state',how='left')
    m=make_map(frame,'state','state_name',float(summary.rate.min()),float(summary.rate.max()),
               selected_state,national=True,alaska=alaska,bounds=bounds)
    event=st_folium(m,key=f"national_{label}_{st.session_state.map_epoch}",height=height,
                   use_container_width=True,returned_objects=['last_object_clicked_popup'])
    clicked=re.search(r'STATE:([A-Z]{2})',str(event.get('last_object_clicked_popup','')))
    if clicked:
        code=clicked.group(1)
        if code in options:
            st.session_state['_pending_state']=code
            st.rerun()
        else:
            st.info('This area has no valid ZCTA estimates in the uploaded dataset.')

if st.session_state.map_depth=='national' or selected_state=='US':
    contiguous=states[~states.state.isin(['AK','HI','PR','GU','VI','MP','AS'])]
    show_national_panel(contiguous,'Contiguous United States',500,bounds=[[24,-125],[50,-66]])
    extras=['AK','HI']+[s for s in ['PR','GU','VI','MP','AS'] if s in summary.state.values]
    panels=st.columns(min(len(extras),3))
    for i,code in enumerate(extras):
        with panels[i%len(panels)]:
            show_national_panel(states[states.state==code],state_names[code],260,alaska=code=='AK')
else:
    st.button('← Back to national map',on_click=back_to_national)
    st.markdown(f'**{state_names[selected_state]} · ZCTAs**')
    shape=state_shapes(selected_state).merge(data[['zcta','rate']],on='zcta',how='left',validate='one_to_one')
    if shape.empty:
        st.info('No ZCTA boundaries available for this state.')
    else:
        m=make_map(shape,'zcta','zcta',float(data.rate.min()),float(data.rate.max()),
                   None if selected_zcta=='All' else selected_zcta,alaska=selected_state=='AK')
        st_folium(m,key=f"state_{selected_state}_{selected_zcta}_{st.session_state.map_epoch}",
                  height=540,use_container_width=True,returned_objects=[])
    st.caption('State-map clicks do not drill down. Use the ZCTA selector to choose a specific area.')

st.divider()
left,right=st.columns(2)
chart_data=summary.rename(columns={'state_name':'Region'}) if selected_state=='US' else subset.rename(columns={'zcta':'Region'})
with left:
    st.subheader('Distribution of state means' if selected_state=='US' else f'ZCTA distribution · {state_names[selected_state]}')
    fig=px.histogram(chart_data,x='rate',nbins=20,color_discrete_sequence=['#318b83'],
                     labels={'rate':'Short sleep duration (%)'})
    fig.update_layout(yaxis_title='States / district' if selected_state=='US' else 'ZCTAs',bargap=0.06,margin=dict(l=10,r=10,t=30,b=10))
    if selected_state!='US':
        fig.add_vline(x=float(subset.rate.mean()),line_dash='dash',line_color='#475569',annotation_text='State mean')
    if selected_zcta!='All':
        fig.add_vline(x=value,line_color='#dc2626',annotation_text=f'ZCTA {selected_zcta}')
    st.plotly_chart(fig,width='stretch')
with right:
    st.subheader('Top 10 states / district' if selected_state=='US' else 'Top 10 ZCTAs in this state')
    top=chart_data.nlargest(10,'rate').sort_values('rate')
    fig=px.bar(top,x='rate',y='Region',orientation='h',text='rate',labels={'rate':'Short sleep duration (%)'})
    fig.update_traces(marker_color=['#dc2626' if str(x)==selected_zcta else '#318b83' for x in top.Region],texttemplate='%{x:.1f}%',textposition='outside',cliponaxis=False)
    fig.update_layout(yaxis=dict(type='category',title=None),xaxis_range=[0,float(top.rate.max())*1.17],margin=dict(l=10,r=10,t=30,b=10))
    st.plotly_chart(fig,width='stretch')
st.caption('Rankings use point estimates; uncertainty intervals are not available in this CSV.')

with st.expander('View and download data'):
    table=summary.copy() if selected_state=='US' else subset[['zcta','state','state_name','rate']].copy()
    if selected_zcta!='All':
        table=table[table.zcta==selected_zcta]
    st.dataframe(table,hide_index=True)
    st.download_button('Download displayed table',table.to_csv(index=False).encode(),f'sleep_{selected_state}_{selected_zcta}.csv','text/csv')
    st.download_button('Download all ZCTA-to-state results',data[['zcta','state','state_name','rate','overlap_share']].to_csv(index=False).encode(),'zcta_state_sleep.csv','text/csv')
with st.expander('Methods and coverage'):
    st.write(report)
    st.write('State assignment uses the largest ZCTA–state polygon intersection in equal-area EPSG:6933; exact ties use state code. Each ZCTA belongs to one state for aggregation. Display polygons are simplified after mapping and clipped to that state. A clipped polygon retains the full-ZCTA estimate.')
    st.write('National scope is the valid ZCTAs in this uploaded file; missing areas are not imputed. The CSV does not retain release/year or estimate-type metadata, so no year or time trend is inferred. District of Columbia is treated as a state-equivalent area.')
    st.markdown('[Census 2020 boundary source](https://www2.census.gov/geo/tiger/GENZ2020/shp/) · [CDC PLACES](https://www.cdc.gov/places/)')
