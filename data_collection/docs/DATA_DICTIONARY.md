# 数据字典

布尔字段严格采用 `true/false/null`；NULL表示网页没有给出，不能解释为false。主记录ID由稳定业务字段与规范化来源生成。

## source_registry

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `source_id` | `object` | 稳定主键 |
| `source_url` | `object` | 网页事实或规范化分类字段 |
| `canonical_url` | `object` | 网页事实或规范化分类字段 |
| `page_title` | `object` | 网页事实或规范化分类字段 |
| `publisher` | `object` | 网页事实或规范化分类字段 |
| `publisher_type` | `object` | 网页事实或规范化分类字段 |
| `source_tier` | `object` | 网页事实或规范化分类字段 |
| `published_at` | `object` | 日期或时间字段，未知时为NULL |
| `updated_at` | `object` | 日期或时间字段，未知时为NULL |
| `accessed_at` | `object` | 日期或时间字段，未知时为NULL |
| `language` | `object` | 网页事实或规范化分类字段 |
| `content_type` | `object` | 网页事实或规范化分类字段 |
| `archive_url` | `object` | 网页事实或规范化分类字段 |
| `http_status_at_collection` | `object` | 日期或时间字段，未知时为NULL |
| `license_or_terms_note` | `object` | 网页事实或规范化分类字段 |
| `is_official` | `bool` | 三态布尔字段 |
| `is_primary_source` | `bool` | 三态布尔字段 |
| `page_hash` | `object` | 网页事实或规范化分类字段 |
| `snapshot_path` | `object` | 网页事实或规范化分类字段 |
| `notes` | `object` | 网页事实或规范化分类字段 |

## record_source_links

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `link_id` | `object` | 稳定主键 |
| `dataset_name` | `object` | 网页事实或规范化分类字段 |
| `record_id` | `object` | 网页事实或规范化分类字段 |
| `source_id` | `object` | 网页事实或规范化分类字段 |
| `evidence_quote` | `object` | 支持该记录的短证据摘录 |
| `evidence_field` | `object` | 网页事实或规范化分类字段 |
| `fact_status` | `object` | observed/derived/assumed，区分观察、推导和假设 |
| `extraction_confidence` | `object` | 网页事实或规范化分类字段 |
| `review_status` | `object` | 网页事实或规范化分类字段 |
| `review_notes` | `object` | 网页事实或规范化分类字段 |

## weather_transport_disruptions

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `record_id` | `object` | 稳定主键 |
| `event_id` | `object` | 稳定主键 |
| `event_name` | `object` | 网页事实或规范化分类字段 |
| `weather_type` | `object` | 网页事实或规范化分类字段 |
| `weather_subtype` | `object` | 网页事实或规范化分类字段 |
| `warning_type` | `object` | 网页事实或规范化分类字段 |
| `warning_level` | `object` | 网页事实或规范化分类字段 |
| `warning_issued_at` | `object` | 日期或时间字段，未知时为NULL |
| `event_start_datetime` | `object` | 日期或时间字段，未知时为NULL |
| `event_end_datetime` | `object` | 日期或时间字段，未知时为NULL |
| `observation_datetime` | `object` | 日期或时间字段，未知时为NULL |
| `recovery_datetime` | `object` | 日期或时间字段，未知时为NULL |
| `district` | `object` | 上海市区级行政名称 |
| `subdistrict` | `object` | 街道或镇名称；网页未明确时为NULL |
| `location_name` | `object` | 网页事实或规范化分类字段 |
| `address_or_segment` | `object` | 网页事实或规范化分类字段 |
| `entity_type` | `object` | 网页事实或规范化分类字段 |
| `entity_name` | `object` | 网页事实或规范化分类字段 |
| `line_or_route` | `object` | 网页事实或规范化分类字段 |
| `direction` | `object` | 网页事实或规范化分类字段 |
| `disruption_type` | `object` | 网页事实或规范化分类字段 |
| `disruption_description` | `object` | 网页事实或规范化分类字段 |
| `quantitative_value` | `float64` | 数值字段，缺少直接证据时为NULL |
| `quantitative_unit` | `object` | 网页事实或规范化分类字段 |
| `baseline_value` | `object` | 网页事实或规范化分类字段 |
| `severity_ordinal` | `object` | 网页事实或规范化分类字段 |
| `service_status_before` | `object` | 网页事实或规范化分类字段 |
| `service_status_during` | `object` | 网页事实或规范化分类字段 |
| `service_status_after` | `object` | 网页事实或规范化分类字段 |
| `affected_population_description` | `object` | 网页事实或规范化分类字段 |
| `temporary_measure` | `object` | 网页事实或规范化分类字段 |
| `recovery_description` | `object` | 网页事实或规范化分类字段 |
| `record_valid_from` | `object` | 日期或时间字段，未知时为NULL |
| `record_valid_to` | `object` | 日期或时间字段，未知时为NULL |
| `current_status` | `object` | active/expired/suspended/replaced/historical/unknown |
| `geocode_lat` | `object` | 网页事实或规范化分类字段 |
| `geocode_lon` | `object` | 网页事实或规范化分类字段 |
| `coordinate_system` | `object` | 网页事实或规范化分类字段 |
| `coordinate_method` | `object` | 网页事实或规范化分类字段 |
| `geocode_confidence` | `object` | 网页事实或规范化分类字段 |
| `primary_source_id` | `object` | 主要来源，外键指向 source_registry.source_id |
| `fact_status` | `object` | observed/derived/assumed，区分观察、推导和假设 |
| `record_confidence` | `object` | high/medium/low；核心导出不接纳low |
| `created_at` | `object` | 日期或时间字段，未知时为NULL |
| `updated_at` | `object` | 日期或时间字段，未知时为NULL |
| `evidence_quote` | `object` | 支持该记录的短证据摘录 |

## weather_transport_calibration

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `calibration_id` | `object` | 稳定主键 |
| `weather_scenario` | `object` | 网页事实或规范化分类字段 |
| `parameter_name` | `object` | 网页事实或规范化分类字段 |
| `spatial_scope` | `object` | 网页事实或规范化分类字段 |
| `time_scope` | `object` | 日期或时间字段，未知时为NULL |
| `estimate_low` | `float64` | 数值字段，缺少直接证据时为NULL |
| `estimate_base` | `float64` | 数值字段，缺少直接证据时为NULL |
| `estimate_high` | `float64` | 数值字段，缺少直接证据时为NULL |
| `unit` | `object` | 网页事实或规范化分类字段 |
| `derivation_method` | `object` | 网页事实或规范化分类字段 |
| `supporting_record_ids` | `object` | 网页事实或规范化分类字段 |
| `supporting_source_ids` | `object` | 网页事实或规范化分类字段 |
| `evidence_strength` | `object` | 网页事实或规范化分类字段 |
| `is_model_assumption` | `bool` | 三态布尔字段 |
| `sensitivity_required` | `bool` | 三态布尔字段 |
| `notes` | `object` | 网页事实或规范化分类字段 |

## elderly_hailing_access

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `service_id` | `object` | 稳定主键 |
| `program_name` | `object` | 网页事实或规范化分类字段 |
| `service_point_name` | `object` | 网页事实或规范化分类字段 |
| `service_type` | `object` | 网页事实或规范化分类字段 |
| `operator_name` | `object` | 网页事实或规范化分类字段 |
| `operator_type` | `object` | 网页事实或规范化分类字段 |
| `platform_or_dispatch_system` | `object` | 网页事实或规范化分类字段 |
| `district` | `object` | 上海市区级行政名称 |
| `subdistrict` | `object` | 街道或镇名称；网页未明确时为NULL |
| `community` | `object` | 网页事实或规范化分类字段 |
| `address` | `object` | 网页事实或规范化分类字段 |
| `coverage_scope` | `object` | 网页事实或规范化分类字段 |
| `coverage_description` | `object` | 网页事实或规范化分类字段 |
| `geocode_lat` | `object` | 网页事实或规范化分类字段 |
| `geocode_lon` | `object` | 网页事实或规范化分类字段 |
| `coordinate_system` | `object` | 网页事实或规范化分类字段 |
| `coordinate_method` | `object` | 网页事实或规范化分类字段 |
| `geocode_confidence` | `object` | 网页事实或规范化分类字段 |
| `launch_date` | `object` | 日期或时间字段，未知时为NULL |
| `valid_from` | `object` | 日期或时间字段，未知时为NULL |
| `valid_to` | `object` | 日期或时间字段，未知时为NULL |
| `last_verified_at` | `object` | 日期或时间字段，未知时为NULL |
| `current_status` | `object` | active/expired/suspended/replaced/historical/unknown |
| `service_hours_text` | `object` | 网页事实或规范化分类字段 |
| `weekday_available` | `object` | 三态布尔字段 |
| `weekend_available` | `object` | 三态布尔字段 |
| `holiday_available` | `object` | 三态布尔字段 |
| `appointment_supported` | `object` | 网页事实或规范化分类字段 |
| `real_time_hailing_supported` | `object` | 日期或时间字段，未知时为NULL |
| `channel_app` | `object` | 三态布尔字段 |
| `channel_miniprogram` | `object` | 三态布尔字段 |
| `channel_phone` | `object` | 三态布尔字段 |
| `channel_smart_screen` | `object` | 三态布尔字段 |
| `channel_self_service_terminal` | `object` | 三态布尔字段 |
| `channel_community_staff` | `object` | 三态布尔字段 |
| `channel_hospital_staff` | `object` | 三态布尔字段 |
| `channel_family_proxy` | `object` | 三态布尔字段 |
| `channel_street_hail` | `object` | 三态布尔字段 |
| `smartphone_required` | `object` | 网页事实或规范化分类字段 |
| `mobile_number_required` | `object` | 网页事实或规范化分类字段 |
| `real_name_required` | `object` | 网页事实或规范化分类字段 |
| `manual_assistance_available` | `object` | 网页事实或规范化分类字段 |
| `cash_payment_supported` | `object` | 网页事实或规范化分类字段 |
| `mobile_payment_supported` | `object` | 网页事实或规范化分类字段 |
| `transport_card_supported` | `object` | 网页事实或规范化分类字段 |
| `family_payment_supported` | `object` | 网页事实或规范化分类字段 |
| `offline_payment_supported` | `object` | 网页事实或规范化分类字段 |
| `passenger_identity_binding` | `object` | 网页事实或规范化分类字段 |
| `eligibility_age_min` | `float64` | 数值字段，缺少直接证据时为NULL |
| `target_population` | `object` | 网页事实或规范化分类字段 |
| `wheelchair_or_accessible_vehicle` | `object` | 网页事实或规范化分类字段 |
| `failure_handling` | `object` | 网页事实或规范化分类字段 |
| `retry_or_transfer_mechanism` | `object` | 网页事实或规范化分类字段 |
| `service_fee` | `object` | 网页事实或规范化分类字段 |
| `subsidy_available` | `object` | 网页事实或规范化分类字段 |
| `usage_limit` | `object` | 网页事实或规范化分类字段 |
| `published_usage_count` | `object` | 网页事实或规范化分类字段 |
| `published_service_count` | `float64` | 数值字段，缺少直接证据时为NULL |
| `primary_source_id` | `object` | 主要来源，外键指向 source_registry.source_id |
| `fact_status` | `object` | observed/derived/assumed，区分观察、推导和假设 |
| `record_confidence` | `object` | high/medium/low；核心导出不接纳low |
| `created_at` | `object` | 日期或时间字段，未知时为NULL |
| `updated_at` | `object` | 日期或时间字段，未知时为NULL |
| `evidence_quote` | `object` | 支持该记录的短证据摘录 |

## ride_hailing_promotion_rules

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `rule_version_id` | `object` | 稳定主键 |
| `platform_name` | `object` | 网页事实或规范化分类字段 |
| `platform_type` | `object` | 网页事实或规范化分类字段 |
| `campaign_name` | `object` | 网页事实或规范化分类字段 |
| `rule_version` | `object` | 网页事实或规范化分类字段 |
| `announcement_date` | `object` | 日期或时间字段，未知时为NULL |
| `valid_from` | `object` | 日期或时间字段，未知时为NULL |
| `valid_to` | `object` | 日期或时间字段，未知时为NULL |
| `last_verified_at` | `object` | 日期或时间字段，未知时为NULL |
| `current_status` | `object` | active/expired/suspended/replaced/historical/unknown |
| `city_scope` | `object` | 网页事实或规范化分类字段 |
| `district_scope` | `object` | 网页事实或规范化分类字段 |
| `geofence_description` | `object` | 网页事实或规范化分类字段 |
| `target_population` | `object` | 网页事实或规范化分类字段 |
| `eligibility_age_min` | `object` | 网页事实或规范化分类字段 |
| `eligibility_age_max` | `object` | 网页事实或规范化分类字段 |
| `new_user_only` | `object` | 网页事实或规范化分类字段 |
| `existing_user_allowed` | `object` | 网页事实或规范化分类字段 |
| `membership_required` | `object` | 网页事实或规范化分类字段 |
| `hospital_trip_required` | `object` | 网页事实或规范化分类字段 |
| `specified_hospital_only` | `object` | 网页事实或规范化分类字段 |
| `origin_restriction` | `object` | 网页事实或规范化分类字段 |
| `destination_restriction` | `object` | 网页事实或规范化分类字段 |
| `time_window_restriction` | `object` | 日期或时间字段，未知时为NULL |
| `weather_triggered` | `object` | 网页事实或规范化分类字段 |
| `weather_trigger_description` | `object` | 网页事实或规范化分类字段 |
| `claim_required` | `object` | 网页事实或规范化分类字段 |
| `claim_channel` | `object` | 网页事实或规范化分类字段 |
| `app_push_required` | `object` | 网页事实或规范化分类字段 |
| `manual_discovery_required` | `object` | 网页事实或规范化分类字段 |
| `auto_credit` | `object` | 三态布尔字段 |
| `auto_apply` | `object` | 三态布尔字段 |
| `app_only` | `object` | 网页事实或规范化分类字段 |
| `miniprogram_supported` | `object` | 网页事实或规范化分类字段 |
| `phone_supported` | `object` | 网页事实或规范化分类字段 |
| `family_proxy_allowed` | `object` | 网页事实或规范化分类字段 |
| `community_proxy_allowed` | `object` | 网页事实或规范化分类字段 |
| `actual_passenger_binding` | `object` | 网页事实或规范化分类字段 |
| `real_name_required` | `object` | 网页事实或规范化分类字段 |
| `payment_method_restriction` | `object` | 网页事实或规范化分类字段 |
| `coupon_type` | `object` | 网页事实或规范化分类字段 |
| `coupon_face_value` | `object` | 网页事实或规范化分类字段 |
| `discount_rate` | `object` | 网页事实或规范化分类字段 |
| `minimum_spend` | `object` | 网页事实或规范化分类字段 |
| `maximum_discount` | `object` | 网页事实或规范化分类字段 |
| `number_of_coupons` | `object` | 网页事实或规范化分类字段 |
| `usage_frequency` | `object` | 网页事实或规范化分类字段 |
| `total_quota` | `object` | 网页事实或规范化分类字段 |
| `first_come_first_served` | `object` | 网页事实或规范化分类字段 |
| `stackable` | `object` | 网页事实或规范化分类字段 |
| `vehicle_type_restriction` | `object` | 网页事实或规范化分类字段 |
| `platform_service_restriction` | `object` | 网页事实或规范化分类字段 |
| `terms_summary` | `object` | 网页事实或规范化分类字段 |
| `expiry_logic` | `object` | 网页事实或规范化分类字段 |
| `rule_change_description` | `object` | 网页事实或规范化分类字段 |
| `primary_source_id` | `object` | 主要来源，外键指向 source_registry.source_id |
| `fact_status` | `object` | observed/derived/assumed，区分观察、推导和假设 |
| `record_confidence` | `object` | high/medium/low；核心导出不接纳low |
| `created_at` | `object` | 日期或时间字段，未知时为NULL |
| `updated_at` | `object` | 日期或时间字段，未知时为NULL |
| `evidence_quote` | `object` | 支持该记录的短证据摘录 |

## subdistrict_elder_support_facts

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `fact_id` | `object` | 稳定主键 |
| `reference_period_start` | `object` | 网页事实或规范化分类字段 |
| `reference_period_end` | `object` | 网页事实或规范化分类字段 |
| `reference_year` | `object` | 网页事实或规范化分类字段 |
| `district` | `object` | 上海市区级行政名称 |
| `subdistrict` | `object` | 街道或镇名称；网页未明确时为NULL |
| `community` | `object` | 网页事实或规范化分类字段 |
| `program_name` | `object` | 网页事实或规范化分类字段 |
| `provider_name` | `object` | 网页事实或规范化分类字段 |
| `provider_type` | `object` | 网页事实或规范化分类字段 |
| `indicator_category` | `object` | 网页事实或规范化分类字段 |
| `indicator_name` | `object` | 网页事实或规范化分类字段 |
| `indicator_value_numeric` | `float64` | 数值字段，缺少直接证据时为NULL |
| `indicator_value_text` | `object` | 网页事实或规范化分类字段 |
| `unit` | `object` | 网页事实或规范化分类字段 |
| `denominator` | `object` | 网页事实或规范化分类字段 |
| `age_scope` | `object` | 网页事实或规范化分类字段 |
| `target_population` | `object` | 网页事实或规范化分类字段 |
| `coverage_scope` | `object` | 网页事实或规范化分类字段 |
| `service_type` | `object` | 网页事实或规范化分类字段 |
| `service_hours_text` | `object` | 网页事实或规范化分类字段 |
| `weekday_available` | `object` | 三态布尔字段 |
| `weekend_available` | `object` | 三态布尔字段 |
| `holiday_available` | `object` | 三态布尔字段 |
| `appointment_required` | `object` | 网页事实或规范化分类字段 |
| `hotline_available` | `object` | 网页事实或规范化分类字段 |
| `hotline_number` | `object` | 网页事实或规范化分类字段 |
| `volunteer_count` | `object` | 网页事实或规范化分类字段 |
| `paired_elder_count` | `object` | 网页事实或规范化分类字段 |
| `pairing_ratio` | `object` | 网页事实或规范化分类字段 |
| `contact_frequency` | `object` | 网页事实或规范化分类字段 |
| `digital_training_sessions` | `object` | 网页事实或规范化分类字段 |
| `digital_training_participants` | `object` | 网页事实或规范化分类字段 |
| `community_hailing_available` | `object` | 网页事实或规范化分类字段 |
| `medical_trip_assistance` | `object` | 网页事实或规范化分类字段 |
| `hospital_companion_service` | `object` | 网页事实或规范化分类字段 |
| `home_visit_service` | `object` | 网页事实或规范化分类字段 |
| `emergency_assistance` | `object` | 网页事实或规范化分类字段 |
| `published_service_count` | `object` | 网页事实或规范化分类字段 |
| `published_beneficiary_count` | `object` | 网页事实或规范化分类字段 |
| `primary_source_id` | `object` | 主要来源，外键指向 source_registry.source_id |
| `fact_status` | `object` | observed/derived/assumed，区分观察、推导和假设 |
| `record_confidence` | `object` | high/medium/low；核心导出不接纳low |
| `created_at` | `object` | 日期或时间字段，未知时为NULL |
| `updated_at` | `object` | 日期或时间字段，未知时为NULL |
| `evidence_quote` | `object` | 支持该记录的短证据摘录 |

## subdistrict_elder_support_features

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `feature_record_id` | `object` | 稳定主键 |
| `reference_year` | `float64` | 数值字段，缺少直接证据时为NULL |
| `district` | `object` | 上海市区级行政名称 |
| `subdistrict` | `object` | 街道或镇名称；网页未明确时为NULL |
| `elderly_population` | `object` | 网页事实或规范化分类字段 |
| `elderly_share` | `object` | 网页事实或规范化分类字段 |
| `age_80_plus_population` | `object` | 网页事实或规范化分类字段 |
| `living_alone_population` | `object` | 网页事实或规范化分类字段 |
| `pure_elderly_households` | `object` | 网页事实或规范化分类字段 |
| `digital_training_intensity` | `object` | 网页事实或规范化分类字段 |
| `volunteer_support_intensity` | `float64` | 数值字段，缺少直接证据时为NULL |
| `community_hailing_access` | `object` | 网页事实或规范化分类字段 |
| `medical_trip_support` | `object` | 网页事实或规范化分类字段 |
| `weekday_support_score` | `object` | 三态布尔字段 |
| `weekend_support_score` | `object` | 三态布尔字段 |
| `formal_assistance_score` | `float64` | 数值字段，缺少直接证据时为NULL |
| `digital_support_score` | `object` | 网页事实或规范化分类字段 |
| `overall_elder_support_score` | `object` | 网页事实或规范化分类字段 |
| `source_fact_ids` | `object` | 网页事实或规范化分类字段 |
| `missingness_rate` | `float64` | 数值字段，缺少直接证据时为NULL |
| `derivation_method` | `object` | 网页事实或规范化分类字段 |
| `feature_confidence` | `object` | 网页事实或规范化分类字段 |
| `sensitivity_required` | `bool` | 三态布尔字段 |
| `created_at` | `object` | 日期或时间字段，未知时为NULL |
| `updated_at` | `object` | 日期或时间字段，未知时为NULL |

## shanghai_nine_prototypes

| 字段 | 导出类型 | 含义/约束 |
|---|---|---|
| `prototype_zone_id` | `object` | 稳定主键 |
| `prototype_zone_type` | `object` | 网页事实或规范化分类字段 |
| `district` | `object` | 上海市区级行政名称 |
| `subdistrict` | `object` | 街道或镇名称；网页未明确时为NULL |
| `selection_reason` | `object` | 网页事实或规范化分类字段 |
| `elder_support_summary` | `object` | 网页事实或规范化分类字段 |
| `public_transport_summary` | `object` | 网页事实或规范化分类字段 |
| `weather_disruption_summary` | `object` | 网页事实或规范化分类字段 |
| `multichannel_hailing_summary` | `object` | 网页事实或规范化分类字段 |
| `data_coverage_score` | `object` | 网页事实或规范化分类字段 |
| `source_fact_ids` | `object` | 网页事实或规范化分类字段 |
| `limitations` | `object` | 网页事实或规范化分类字段 |
