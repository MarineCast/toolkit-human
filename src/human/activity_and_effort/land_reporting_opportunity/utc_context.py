"""Bounded UTC solar/civil-calendar/visibility scenarios, not measured human effort.

See docs/utc-scientific-methods.md for equations, support and qualification limits.
No legacy daily score or precipitation response is reinterpreted by this module.
"""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from importlib.resources import files
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit
import tzdata

METHOD_VERSION = 'utc-solar-civil-linear-weather-v1-research'
ZONES = {'America/Los_Angeles', 'America/Vancouver'}


def timezone_provenance(name: str) -> dict:
    if name not in ZONES:
        raise ValueError('Only the reviewed WA/BC civil zones are supported')
    data = files('tzdata.zoneinfo').joinpath(name).read_bytes()
    return {'zone': name, 'tzdata_version': tzdata.__version__,
            'iana_version': tzdata.IANA_VERSION, 'tzif_sha256': sha256(data).hexdigest()}


def _zone(name: str) -> ZoneInfo:
    timezone_provenance(name)
    with files('tzdata.zoneinfo').joinpath(name).open('rb') as stream:
        return ZoneInfo.from_file(stream, key=name)


def _interval(start, end) -> tuple[pd.Timestamp, pd.Timestamp]:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if pd.isna(start) or pd.isna(end) or start.tzinfo is None or end.tzinfo is None:
        raise ValueError('Interval boundaries must be timezone-aware')
    start, end = start.tz_convert('UTC'), end.tz_convert('UTC')
    if not 0 < (end-start).total_seconds() <= 172800:
        raise ValueError('Require a positive interval of at most 48 hours')
    if start < pd.Timestamp('2000-01-01', tz='UTC') or end > pd.Timestamp('2036-01-01', tz='UTC'):
        raise ValueError('Reviewed date range is 2000 through 2035')
    return start, end


def solar_altitude_deg(unix_seconds, latitude: float, longitude: float):
    """USNO approximate solar coordinates + equation of time; east-positive longitude.

    UTC approximates UT1/TT here. No horizon/refraction correction is added to the
    geometric center altitude; the daylight threshold separately specifies it.
    """
    if not np.isfinite([latitude, longitude]).all() or abs(latitude)>60 or abs(longitude)>180:
        raise ValueError('Solar integration supports finite latitude within +/-60 and longitude +/-180')
    seconds = np.asarray(unix_seconds, dtype=float)
    d = seconds/86400 + 2440587.5 - 2451545.0
    g = np.deg2rad((357.529 + .98560028*d) % 360)
    q = (280.459 + .98564736*d) % 360
    lam = np.deg2rad(q + 1.915*np.sin(g) + .020*np.sin(2*g))
    obliquity = np.deg2rad(23.439 - .00000036*d)
    ra = np.rad2deg(np.arctan2(np.cos(obliquity)*np.sin(lam), np.cos(lam)))
    declination = np.arcsin(np.sin(obliquity)*np.sin(lam))
    equation_minutes = 4*((q-ra+180) % 360-180)
    hour_angle = np.deg2rad((seconds % 86400/60 + 4*longitude + equation_minutes)/4-180)
    lat = np.deg2rad(latitude)
    sine = np.sin(lat)*np.sin(declination) + np.cos(lat)*np.cos(declination)*np.cos(hour_angle)
    return np.rad2deg(np.arcsin(np.clip(sine, -1, 1)))


def daylight_intervals(start, end, latitude: float, longitude: float) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Intervals above -0.8333 degrees: standard upper-limb daylight, level horizon."""
    start, end = _interval(start, end)
    a, b = start.timestamp(), end.timestamp()
    grid = np.linspace(a, b, int(np.ceil((b-a)/600))+1)
    values = solar_altitude_deg(grid, latitude, longitude) + .8333
    roots = [a, b]
    for i in range(len(grid)-1):
        if values[i] == 0:
            roots.append(grid[i])
        elif values[i]*values[i+1] < 0:
            roots.append(brentq(lambda x: float(solar_altitude_deg(x, latitude, longitude)+.8333),
                                grid[i], grid[i+1], xtol=.01))
    edges = sorted(set(roots))
    return [(pd.Timestamp(x, unit='s', tz='UTC'), pd.Timestamp(y, unit='s', tz='UTC'))
            for x,y in zip(edges[:-1], edges[1:])
            if solar_altitude_deg((x+y)/2, latitude, longitude) >= -.8333]


def civil_calendar_segments(calendar: pd.DataFrame, start, end, *, timezone: str) -> pd.DataFrame:
    """Exact overlap of civil-day context values, not proration of daily observations.

    Input columns local_date/value; values are finite [0,1] context or null. Missing
    dates stay unknown. One explicit calendar/jurisdiction variant per call.
    """
    start, end = _interval(start, end)
    zone = _zone(timezone)
    dates = pd.to_datetime(calendar['local_date'], errors='raise')
    if dates.isna().any() or dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
        raise ValueError('Calendar keys must be non-null naive civil dates')
    if dates.duplicated().any():
        raise ValueError('Duplicate civil calendar dates')
    values = pd.to_numeric(calendar['value'], errors='raise').to_numpy(dtype=float, na_value=np.nan)
    known = values[~np.isnan(values)]
    if not np.isfinite(known).all() or ((known<0)|(known>1)).any():
        raise ValueError('Calendar values must be in [0,1] or null')
    lookup = dict(zip(dates.dt.date, values))
    day = start.tz_convert(zone).date()
    last = end.tz_convert(zone).date()
    rows = []
    while day <= last:
        left = pd.Timestamp(day).tz_localize(zone).tz_convert('UTC')
        right = pd.Timestamp(day+timedelta(days=1)).tz_localize(zone).tz_convert('UTC')
        left, right = max(left,start), min(right,end)
        if right > left:
            rows.append({'start_utc':left,'end_utc':right,'local_date':day,
                         'value':lookup.get(day,np.nan),'seconds':(right-left).total_seconds()})
        day += timedelta(days=1)
    return pd.DataFrame(rows)


def _weather(samples: pd.DataFrame):
    required = ['valid_time_utc','visibility_km','wind_speed_m_s','sample_status']
    if not set(required).issubset(samples):
        raise ValueError(f'Weather requires {required}')
    for value in samples.valid_time_utc:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError('Weather times must be non-null timezone-aware instants')
    times = pd.to_datetime(samples.valid_time_utc, utc=True)
    if times.duplicated().any():
        raise ValueError('Duplicate weather sample instants')
    if not samples.sample_status.isin(['valid','unavailable']).all():
        raise ValueError('Unknown weather sample status')
    order = np.argsort(times.to_numpy())
    seconds = np.array([t.timestamp() for t in times])[order]
    columns = {}
    for name in ['visibility_km','wind_speed_m_s','precip_rate_mm_hr']:
        raw = samples[name] if name in samples else pd.Series(np.nan, index=samples.index)
        values = pd.to_numeric(raw, errors='raise').to_numpy(dtype=float,na_value=np.nan,copy=True)
        known = values[~np.isnan(values)]
        if not np.isfinite(known).all() or (known<0).any():
            raise ValueError(f'{name} must be finite nonnegative or null')
        values[samples.sample_status.to_numpy()!='valid'] = np.nan
        columns[name] = values[order]
    return seconds, columns


def _interpolate(query, times, values, maximum_gap_seconds):
    if len(times)<2:
        return np.full(len(query),np.nan)
    left = np.searchsorted(times,query,side='right')-1
    safe = np.clip(left,0,len(times)-2)
    span = times[safe+1]-times[safe]
    supported = (left>=0)&(left<len(times)-1)&(span<=maximum_gap_seconds)
    result = values[safe]+(values[safe+1]-values[safe])*(query-times[safe])/span
    return np.where(supported,result,np.nan)


def _product(*arrays):
    """An explicitly known zero establishes a zero product despite unknown factors."""
    stack = np.asarray(arrays)
    with np.errstate(invalid='ignore'):
        result = np.prod(stack,axis=0)
    return np.where(np.any(stack==0,axis=0),0,result)


def _summarize(values, dt, required, total):
    known = np.isfinite(values)
    required_seconds = float(np.sum(dt*required))
    known_seconds = float(np.sum(dt*required*known))
    coverage = known_seconds/required_seconds if required_seconds else 1.0
    contribution = float(np.sum(np.where(known,values,0)*dt)/total)
    unknown_max = float(np.sum(dt*required*~known)/total)
    return {'value':contribution if known_seconds>0 or required_seconds==0 else None,
            'status':'derived' if coverage>=1-1e-12 else ('partial' if known_seconds>0 else 'unavailable'),
            'coverage_fraction':coverage,'required_seconds':required_seconds,
            'known_required_seconds':known_seconds,'lower_bound':contribution,
            'upper_bound':min(1.0,contribution+unknown_max)}


def integrate_land_context(start, end, *, latitude: float, longitude: float,
                           calendar: pd.DataFrame, timezone: str, weather: pd.DataFrame,
                           distance_km: float, maximum_gap_hours: float=6,
                           visibility_transition_fraction: float=.2,
                           visibility_minimum_transition_km: float=1,
                           wind_midpoint_m_s: float=5.5, wind_slope_m_s: float=1.5,
                           wind_exponent: float=.7, precipitation_half_mm_day: float=10,
                           precipitation_exponent: float=.3,
                           quadrature_seconds: float=60) -> dict:
    """Integrate bounded research factors at the declared location/interval.

    Weather is piecewise linear ONLY between finite adjacent valid samples separated
    by at most maximum_gap_hours; no extrapolation or bridging null samples. Results
    are full-interval available contributions, never renormalized over available time.
    This defines a new UTC scenario, not a relabeling of the legacy daily score.
    Rain totals integrate forecast rates; the daily rain response is applied only
    for a full UTC day with complete rate support (or a proven zero product).
    """
    start,end = _interval(start,end)
    positive = [maximum_gap_hours,visibility_transition_fraction,
                visibility_minimum_transition_km,wind_slope_m_s,quadrature_seconds,
                wind_exponent,precipitation_half_mm_day,precipitation_exponent]
    if not np.isfinite([*positive,distance_km,wind_midpoint_m_s]).all() or min(positive)<=0 or distance_km<0 or wind_midpoint_m_s<0:
        raise ValueError('Invalid physical/method parameter')
    if quadrature_seconds>300 or quadrature_seconds<1 or maximum_gap_hours>6:
        raise ValueError('Quadrature must be 1..300 seconds; maximum weather gap <=6 hours')
    solar = daylight_intervals(start,end,latitude,longitude)
    civil = civil_calendar_segments(calendar,start,end,timezone=timezone)
    times, weather_values = _weather(weather)
    a,b = start.timestamp(),end.timestamp()
    is_utc_day = start==start.normalize() and end-start==pd.Timedelta(days=1)
    edges = {a,b}
    for x,y in solar:edges.update([x.timestamp(),y.timestamp()])
    for row in civil.itertuples():edges.update([row.start_utc.timestamp(),row.end_utc.timestamp()])
    edges.update(float(t) for t in times if a<t<b)
    edges = sorted(edges)
    segments = [(x,y) for x,y in zip(edges[:-1],edges[1:])]

    def calculate(step):
        query,dt = [],[]
        for x,y in segments:
            n = int(np.ceil((y-x)/step));width=(y-x)/n
            query.extend(x+(np.arange(n)+.5)*width);dt.extend([width]*n)
        query,dt=np.asarray(query),np.asarray(dt)
        day = np.zeros(len(query))
        for x,y in solar:day[(query>=x.timestamp())&(query<y.timestamp())]=1
        cal = np.full(len(query),np.nan)
        for row in civil.itertuples():
            cal[(query>=row.start_utc.timestamp())&(query<row.end_utc.timestamp())]=row.value
        visibility = _interpolate(query,times,weather_values['visibility_km'],maximum_gap_hours*3600)
        wind = _interpolate(query,times,weather_values['wind_speed_m_s'],maximum_gap_hours*3600)
        v = expit((visibility-distance_km)/np.maximum(visibility_minimum_transition_km,visibility_transition_fraction*visibility))
        v[visibility==0]=0
        w = expit((wind_midpoint_m_s-wind)/wind_slope_m_s)**wind_exponent
        rain = _interpolate(query,times,weather_values['precip_rate_mm_hr'],maximum_gap_hours*3600)
        rain_known = np.isfinite(rain)
        rain_coverage = float(np.sum(dt*rain_known)/(b-a))
        rain_available_mm = float(np.sum(np.where(rain_known,rain,0)*dt)/3600)
        rain_total = rain_available_mm if rain_coverage>=1-1e-12 else None
        rain_factor = (1+rain_total/precipitation_half_mm_day)**(-precipitation_exponent) if rain_total is not None and is_utc_day else np.nan
        arrays={'calendar_context':cal,'daylight_calendar':_product(day,cal),
                'daylight_visibility':_product(day,v),
                'daylight_calendar_visibility':_product(day,cal,v),
                'daylight_calendar_visibility_wind':_product(day,cal,v,w)}
        if is_utc_day:
            arrays['daylight_calendar_weather'] = _product(day,cal,v,w,np.full(len(day),rain_factor))
        metrics = {name:_summarize(value,dt,np.ones(len(day)) if name=='calendar_context' else day,b-a)
                   for name,value in arrays.items()}
        return metrics, {'total_mm':rain_total,'available_integral_mm':rain_available_mm,
                         'coverage_fraction':rain_coverage,
                         'status':'derived' if rain_total is not None else ('partial' if rain_coverage>0 else 'unavailable'),
                         'interpretation':'Integral of interpolated instantaneous forecast rates, not observed accumulation'}

    (coarse,_),(fine,rain_summary) = calculate(quadrature_seconds),calculate(quadrature_seconds/2)
    for name in fine:
        fine[name]['quadrature_refinement_delta'] = abs(fine[name]['lower_bound']-coarse[name]['lower_bound'])
    return {'method_version':METHOD_VERSION,'valid_start_utc':start.isoformat(),'valid_end_utc':end.isoformat(),
            'temporal_kind':'utc_day' if is_utc_day else 'utc_interval',
            'interval_seconds':b-a,'latitude':latitude,'longitude':longitude,'timezone':timezone_provenance(timezone),
            'daylight_seconds':sum((y-x).total_seconds() for x,y in solar),
            'daylight_fraction':sum((y-x).total_seconds() for x,y in solar)/(b-a),
            'solar_intervals':[[x.isoformat(),y.isoformat()] for x,y in solar],
            'metrics':fine,'precipitation_estimate':rain_summary,'parameters':{'distance_km':distance_km,'maximum_gap_hours':maximum_gap_hours,
                'quadrature_seconds':quadrature_seconds/2,'visibility_transition_fraction':visibility_transition_fraction,
                'visibility_minimum_transition_km':visibility_minimum_transition_km,
                'wind_midpoint_m_s':wind_midpoint_m_s,'wind_slope_m_s':wind_slope_m_s,
                'wind_exponent':wind_exponent,'precipitation_half_mm_day':precipitation_half_mm_day,
                'precipitation_exponent':precipitation_exponent},
            'qualification':'bounded_method_pilot; inferred context, not measured effort; independent review required'}
