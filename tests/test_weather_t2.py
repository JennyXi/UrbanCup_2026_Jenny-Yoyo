import sys, importlib.util

spec = importlib.util.spec_from_file_location('custom_envs_weather', 'custom/envs/weather.py')
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

# deterministic seed
mod.init_rng(42)

# configure numeric params for tests
mod.CONFIG.weather_cancel_rate_base_extreme_heat = 0.2
mod.CONFIG.weather_cancel_rate_base_heavy_rain = 0.5
mod.CONFIG.cancel_rate_modifier_medical = 0.5
mod.CONFIG.cancel_rate_modifier_work = 0.8
mod.CONFIG.cancel_rate_modifier_daily = 1.0
mod.CONFIG.age_sensitivity_modifier_18_39 = 0.9
mod.CONFIG.age_sensitivity_modifier_40_59 = 1.0
mod.CONFIG.age_sensitivity_modifier_60_plus = 1.05
mod.CONFIG.ride_hailing_preference_shift_extreme_heat = 0.1
mod.CONFIG.ride_hailing_preference_shift_heavy_rain = 0.2

mod.validate_purpose_ordering()
mod.validate_heavy_vs_heat_stronger()

# helper
def make_leg(day, time, purpose, age_group):
    return {'day':day,'departure_time':time,'purpose':purpose,'age_group':age_group}

# 1. W0 all neutral
mod.set_week('W0')
legs = [make_leg('Tuesday','12:00','daily','40-59'), make_leg('Wednesday','09:00','work','18-39')]
for l in legs:
    mod.annotate_leg_with_weather(l)
    mod.sample_weather_cancel_for_leg(l, {'age_group':l['age_group']})

assert all(l['weather_week']=='W0' and l['weather_type']=='normal' and l['weather_event_active']==False and l['trip_continues']==True and l['ride_hailing_preference_shift']==0 for l in legs)
print('Test1 W0 OK')

# 2-4. W1 windows 11:00 - 18:00 inclusive left-closed right-open
mod.set_week('W1')
leg_in_11 = make_leg('Tuesday','11:00','daily','40-59')
leg_in_1759 = make_leg('Tuesday','17:59','daily','40-59')
leg_out_1800 = make_leg('Tuesday','18:00','daily','40-59')
for l in (leg_in_11, leg_in_1759, leg_out_1800):
    mod.annotate_leg_with_weather(l)

assert leg_in_11['weather_event_active'] == True
assert leg_in_1759['weather_event_active'] == True
assert leg_out_1800['weather_event_active'] == False
print('Test2-4 W1 window boundaries OK')

# 5-6. W2 three windows recognition and right-open end
mod.set_week('W2')
mod.set_w2_windows([('Tuesday','07:00','10:00'),('Tuesday','15:00','16:00'),('Wednesday','09:00','10:00')])
leg_w2_1 = make_leg('Tuesday','07:00','work','18-39')
leg_w2_1_end = make_leg('Tuesday','10:00','work','18-39')
leg_w2_2 = make_leg('Tuesday','15:30','daily','60+')
leg_w2_3 = make_leg('Wednesday','09:30','medical','40-59')
for l in (leg_w2_1,leg_w2_1_end,leg_w2_2,leg_w2_3):
    mod.annotate_leg_with_weather(l)

assert leg_w2_1['weather_event_active'] == True
assert leg_w2_1_end['weather_event_active'] == False
assert leg_w2_2['weather_event_active'] == True
assert leg_w2_3['weather_event_active'] == True
print('Test5-6 W2 windows OK')

# 7-12. Purpose modifiers ordering and numeric probability range, heavy>heat
mod.CONFIG.weather_cancel_rate_base_extreme_heat = 0.2
mod.CONFIG.weather_cancel_rate_base_heavy_rain = 0.6
# check ordering
mod.validate_purpose_ordering()
mod.validate_heavy_vs_heat_stronger()

# compute some probabilities
p_med_heat = mod._compute_p_weather_cancel('extreme_heat','medical','18-39')
p_work_heat = mod._compute_p_weather_cancel('extreme_heat','work','18-39')
p_daily_heat = mod._compute_p_weather_cancel('extreme_heat','daily','18-39')
assert 0 <= p_med_heat <= 1
assert p_med_heat < p_work_heat < p_daily_heat
p_med_rain = mod._compute_p_weather_cancel('heavy_rain','medical','18-39')
assert p_med_rain > p_med_heat
print('Test7-12 purpose and heavy>heat and range OK')

# 13-14 sampling once and seed reproducibility
mod.init_rng(999)
leg_sample = make_leg('Tuesday','15:30','daily','40-59')
mod.annotate_leg_with_weather(leg_sample)
res1 = mod.sample_weather_cancel_for_leg(leg_sample, {'age_group':'40-59'})
# second call should not change
res2 = mod.sample_weather_cancel_for_leg(leg_sample, {'age_group':'40-59'})
assert res1 == res2
# reproducibility
mod.init_rng(555)
leg_a = make_leg('Tuesday','15:30','daily','40-59')
leg_b = make_leg('Tuesday','15:30','daily','40-59')
mod.annotate_leg_with_weather(leg_a); mod.annotate_leg_with_weather(leg_b)
res_a = mod.sample_weather_cancel_for_leg(leg_a, {'age_group':'40-59'})
mod.init_rng(555)
res_b = mod.sample_weather_cancel_for_leg(leg_b, {'age_group':'40-59'})
assert res_a == res_b
print('Test13-14 sampling single and reproducible OK')

# 15. outbound cancel invalidates return
out = make_leg('Tuesday','15:30','daily','40-59')
mod.init_rng(1234)
out = make_leg('Tuesday','15:30','daily','40-59')
ret = make_leg('Tuesday','20:00','daily','40-59')
# Do NOT annotate return before processing outbound; process_outbound_return enforces ordering.
mod.set_week('W2')
mod.set_w2_windows([('Tuesday','15:00','16:00'),('Wednesday','09:00','10:00'),('Thursday','08:00','09:00')])
# set params so cancellation likely
mod.CONFIG.weather_cancel_rate_base_heavy_rain = 0.9
mod.CONFIG.cancel_rate_modifier_daily = 1.0
mod.CONFIG.age_sensitivity_modifier_40_59 = 1.0
mod.init_rng(1)
# process outbound then return
ob_cont, rt_cont = mod.process_outbound_return(out, ret, {'age_group':'40-59'}, outbound_trip_completed=True)
# if outbound was cancelled then return state is stored internally; leg must not contain internal markers
if not ob_cont:
    try:
        mod.assert_no_internal_markers(ret)
    except AssertionError:
        raise
print('Test15 outbound->return invalidation OK (if outbound cancelled)')

# 16. outbound and return use own times
mod.set_week('W1')
out2 = make_leg('Tuesday','11:30','daily','40-59')
ret2 = make_leg('Tuesday','19:00','daily','40-59')
mod.annotate_leg_with_weather(out2); mod.annotate_leg_with_weather(ret2)
assert out2['weather_event_active'] == True
assert ret2['weather_event_active'] == False
print('Test16 outbound/return independent weather OK')

# Exposure uses the actual leg interval: a trip departing before the event but
# arriving after it starts is exposed.
interval_leg = make_leg('Tuesday','10:50','daily','40-59')
interval_leg['arrival_time'] = '11:10'
mod.annotate_leg_with_weather(interval_leg)
assert interval_leg['weather_event_active'] == True
print('Test16b interval-overlap weather exposure OK')

# 17-19 supply multipliers are owned by the independent weather-supply layer
mod.set_week('W1')
leg_w1 = make_leg('Tuesday','12:00','daily','40-59')
mod.annotate_leg_with_weather(leg_w1)
mod.set_week('W2')
mod.set_w2_windows([('Tuesday','11:00','13:00'),('Wednesday','09:00','10:00'),('Thursday','08:00','09:00')])
leg_w2 = make_leg('Tuesday','12:00','daily','40-59')
mod.annotate_leg_with_weather(leg_w2)
assert 'bus_time_multiplier' not in leg_w2 and 'ride_hailing_time_multiplier' not in leg_w2
print('Test17-19 supply multipliers not duplicated in T2 OK')

# 20 metro unchanged (not present in outputs)
assert 'metro_time_multiplier' not in leg_w2
print('Test20 metro unchanged OK')

# 21 event-out resets
mod.set_week('W0')
leg_neu = make_leg('Tuesday','12:00','daily','40-59')
mod.annotate_leg_with_weather(leg_neu)
assert leg_neu['ride_hailing_preference_shift']==0
print('Test21 reset OK')

# 22 output fields are behavioral/weather labels only
allowed = {'weather_week','weather_type','weather_event_active','trip_continues','ride_hailing_preference_shift'}
extras = set(leg_w2.keys()) - allowed
# remove common input keys
for k in ['day','departure_time','purpose','age_group','_weather_sampled','awaits_outbound_completion','invalidated_by_outbound']:
    extras.discard(k)
assert extras <= allowed
print('Test22 output field restriction OK')

# 25. non-numeric parameters must raise when sampling
mod.init_rng(7)
mod.CONFIG.weather_cancel_rate_base_extreme_heat = 'to_be_calibrated'
mod.set_week('W1')
leg_bad = make_leg('Tuesday','12:00','daily','40-59')
mod.annotate_leg_with_weather(leg_bad)
try:
    mod.sample_weather_cancel_for_leg(leg_bad, {'age_group':'40-59'})
    raise AssertionError('Expected ValueError due to non-numeric params')
except ValueError:
    print('Test25 non-numeric params raise OK')

# 26. calling process_outbound_return when return already annotated and outbound not completed should raise
mod.set_week('W0')
o = make_leg('Tuesday','12:00','daily','40-59')
r = make_leg('Tuesday','12:00','daily','40-59')
mod.annotate_leg_with_weather(r)
try:
    mod.process_outbound_return(o, r, {'age_group':'40-59'}, outbound_trip_completed=False)
    raise AssertionError('Expected no annotation-before-outbound error')
except ValueError:
    print('Test26 prevent premature return annotate OK')

# 27. final output must not include internal markers
leg_final = make_leg('Tuesday','12:00','daily','40-59')
mod.set_week('W2')
mod.set_w2_windows([('Tuesday','11:00','13:00'),('Wednesday','09:00','10:00'),('Thursday','08:00','09:00')])
mod.annotate_leg_with_weather(leg_final)
mod.sample_weather_cancel_for_leg(leg_final, {'age_group':'40-59'})
# assert no internal markers in dict
for mk in ('_weather_sampled','invalidated_by_outbound','awaits_outbound_completion'):
    assert mk not in leg_final
print('Test27 final output marker cleanup OK')

# 23 base_time not overwritten
base = {'bus_base_time':30,'ride_hailing_base_time':20}
leg_time = make_leg('Tuesday','12:00','daily','40-59')
leg_time.update(base)
mod.set_week('W2')
mod.set_w2_windows([('Tuesday','11:00','13:00'),('Wednesday','09:00','10:00'),('Thursday','08:00','09:00')])
mod.annotate_leg_with_weather(leg_time)
assert leg_time['bus_base_time']==30 and leg_time['ride_hailing_base_time']==20
print('Test23 base_time preserved OK')

# 24 no final mode/order generation
assert 'final_mode' not in leg_time and 'orders' not in leg_time
print('Test24 no final mode/order OK')

# compute trip_continue_rate per group
legs_for_stats = [leg_w2, leg_w2, leg_w2]  # small sample - just ensure function runs
for l in legs_for_stats:
    l['age_group']='40-59'; l['purpose']='daily'
stats = mod.compute_trip_continue_rates(legs_for_stats)
print('Stats sample:', stats)

print('All tests passed (subject to probabilistic outcomes).')

# Detailed activity purposes map to the three weather-sensitivity groups.
assert mod.map_activity_to_weather_purpose('work') == 'work'
assert mod.map_activity_to_weather_purpose('medical') == 'medical'
for activity_purpose in (
    'shopping', 'social_leisure', 'visit',
    'out_of_home_family_care', 'out_of_home_family_activity',
):
    assert mod.map_activity_to_weather_purpose(activity_purpose) == 'daily'
for removed_purpose in ('social', 'leisure'):
    try:
        mod.map_activity_to_weather_purpose(removed_purpose)
        raise AssertionError('legacy purpose unexpectedly accepted')
    except ValueError:
        pass
