"""Public static names; original source/audit schemas retain their names."""

STATIC_NAMES_V3 = {
    'h3_index': 'h3_index',
    'h3_resolution': 'h3_resolution',
    'cell_area_km2': 'cell_area_km2',
    'population_count__coverage_status': 'population_count_coverage_status',
    'population_count__unverified_geographic_support': 'population_count_unverified_geographic_support',
    'population_within_25km__coverage_status': 'population_within_25km_coverage_status',
    'population_within_25km__unverified_geographic_support': 'population_within_25km_unverified_geographic_support',
    'population_within_50km__coverage_status': 'population_within_50km_coverage_status',
    'population_within_50km__unverified_geographic_support': 'population_within_50km_unverified_geographic_support',
    'population_within_100km__coverage_status': 'population_within_100km_coverage_status',
    'population_within_100km__unverified_geographic_support': 'population_within_100km_unverified_geographic_support',
    'population_count_collected': 'population_count_collected',
    'population_within_25km_collected': 'population_within_25km_collected',
    'population_within_50km_collected': 'population_within_50km_collected',
    'population_within_100km_collected': 'population_within_100km_collected',
    'population_density_per_km2_collected': 'population_density_per_km2_collected',
    'boat_launch_access__BOAT_LAUNCH_COUNT': 'boat_launch_record_count',
    'boat_launch_access__VERIFIED_PUBLIC_BOAT_LAUNCH_COUNT': 'source_public_boat_launch_record_count',
    'public_shore_access__TOTAL_MARINE_SHORELINE_M': 'mapped_marine_shoreline_length_m',
    'public_shore_access__PUBLIC_ACCESS_EVIDENCE_STATE': 'shore_access_evidence_state',
    'public_shore_access__PUBLIC_ACCESS_SITE_COUNT': 'shore_access_record_count',
    'public_shore_access__VERIFIED_PUBLIC_ACCESS_SITE_COUNT': 'verified_public_shore_access_record_count',
    'public_shore_access__OSM_EXPLICIT_PUBLIC_ACCESS_SITE_COUNT': 'osm_public_shore_access_record_count',
    'public_shore_access__MAPPED_ACCESS_UNKNOWN_SITE_COUNT': 'unknown_shore_access_record_count',
    'public_shore_access__RESTRICTED_ACCESS_SITE_COUNT': 'restricted_shore_access_record_count',
    'places__CATALOG_RECORD_COUNT': 'place_record_count',
    'ports__SOURCE_RECORD_COUNT': 'port_record_count',
}

AUDIT_ONLY_COLUMNS = frozenset({
    'cell_area_km2',
    *(name for name in STATIC_NAMES_V3.values() if name.endswith(('_coverage_status', '_unverified_geographic_support'))),
})
STATIC_NAMES_V4 = {old:new for old,new in STATIC_NAMES_V3.items() if new not in AUDIT_ONLY_COLUMNS}
STATIC_NAMES = {
    'h3_index': 'h3_index',
    'h3_resolution': 'h3_resolution',
    'population_count_collected': 'population_count',
    'population_within_25km_collected': 'population_within_25km',
    'population_within_50km_collected': 'population_within_50km',
    'population_within_100km_collected': 'population_within_100km',
    'population_density_per_km2_collected': 'population_density_per_km2',
    'boat_launch_access__BOAT_LAUNCH_COUNT': 'boat_launch_count',
    'public_shore_access__PUBLIC_ACCESS_SITE_COUNT': 'shore_access_site_count',
    'places__CATALOG_RECORD_COUNT': 'place_record_count',
    'ports__SOURCE_RECORD_COUNT': 'has_port',
}
COUNT_COLUMNS = {'boat_launch_count', 'shore_access_site_count', 'place_record_count'}
TRANSFORMED_SOURCE_COLUMNS = {raw for raw,public in STATIC_NAMES.items() if public in COUNT_COLUMNS or public == 'has_port'}
PRESENCE_FROM_COUNTS = {'has_boat_launch':'boat_launch_count', 'has_shore_access_site':'shore_access_site_count', 'has_place':'place_record_count'}
PRESENCE_COLUMNS = {'has_port', *PRESENCE_FROM_COUNTS}
COMPACT_SCHEMAS = {"human.study.static.v2", "human.study.static.v3", "human.study.static.v4", "human.study.static.v5", "human.study.static.v6"}
