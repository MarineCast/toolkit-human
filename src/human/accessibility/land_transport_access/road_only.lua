-- Wrapper around the pinned OSRM car profile, copied beside it by routing.py.
local source = debug.getinfo(1, 'S').source:sub(2)
local directory = source:match('(.*/)') or './'
package.path = directory .. '?.lua;' .. package.path
local car = dofile(directory .. 'car.lua')
local process_car_way = car.process_way

car.process_way = function(profile, way, result, relations)
  local route = way:get_value_by_key('route')
  local railway = way:get_value_by_key('railway')
  if route == 'ferry' or route == 'shuttle_train' or railway == 'shuttle_train'
      or way:get_value_by_key('ferry') == 'yes' then
    return
  end
  process_car_way(profile, way, result, relations)
end
return car
