"""Boundary choropleths with hover values and optional state click navigation."""
import json
import folium
import pandas as pd
from branca.colormap import LinearColormap
from shapely.ops import transform


def alaska_west(geometry):
    # Keep the Aleutians beside Alaska instead of spanning the whole world.
    def shift(x,y,z=None):
        try:
            return tuple(v-360 if v>0 else v for v in x), y
        except TypeError:
            return (x-360 if x>0 else x),y
    return transform(shift,geometry)


def make_map(frame, id_col, name_col, vmin, vmax, selected=None,
             national=False, alaska=False, bounds=None):
    frame=frame.copy()
    if alaska:
        frame.geometry=frame.geometry.map(alaska_west)
    frame['Region']=frame[name_col].astype(str)
    frame['Short sleep']=frame.rate.map(lambda x: 'No data' if pd.isna(x) else f'{x:.2f}%')
    fields=['Region','Short sleep']
    if 'n' in frame:
        frame['Valid ZCTAs']=frame.n.fillna(0).astype(int)
        fields.append('Valid ZCTAs')
    if national:
        frame['navigate']=frame[id_col].map(lambda x:f'STATE:{x}')
    cmap=LinearColormap(['#edf8fb','#b2e2e2','#66c2a4','#238b45','#005824'],vmin=vmin,vmax=max(vmax,vmin+0.01))
    cmap.caption='Short sleep duration (%) — same scale across maps at this level'
    m=folium.Map(location=[39,-98],zoom_start=4,tiles=None,prefer_canvas=True,
                 control_scale=True,zoom_control=True)
    folium.TileLayer('OpenStreetMap', name='Optional street basemap',show=False).add_to(m)
    def style(feature):
        p=feature['properties']; value=p.get('rate')
        chosen=str(p[id_col])==str(selected)
        return {'fillColor':'#dadfe5' if value is None else cmap(value),
                'color':'#dc2626' if chosen else '#64748b',
                'weight':3 if chosen else (0.8 if national else 0.4),
                'fillOpacity':0.9}
    geo=folium.GeoJson(json.loads(frame.to_json()),style_function=style,
        highlight_function=lambda f:{'weight':3,'fillOpacity':1},
        tooltip=folium.GeoJsonTooltip(fields=fields,sticky=False),
        popup=folium.GeoJsonPopup(fields=['navigate'],labels=False) if national else None)
    geo.add_to(m)
    # Draw selection last so neighboring polygons cannot cover its red outline.
    chosen=frame[frame[id_col].astype(str)==str(selected)]
    if not chosen.empty:
        folium.GeoJson(json.loads(chosen.to_json()),
            style_function=lambda f:{'color':'#dc2626','weight':3,'fillOpacity':0},
            tooltip=folium.GeoJsonTooltip(fields=fields),
            popup=folium.GeoJsonPopup(fields=['navigate'],labels=False) if national else None).add_to(m)
    if bounds is None:
        x0,y0,x1,y1=frame.total_bounds
        bounds=[[float(y0),float(x0)],[float(y1),float(x1)]]
    m.fit_bounds(bounds,padding=(15,15))
    cmap.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    return m
