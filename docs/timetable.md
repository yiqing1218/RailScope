# Timetable and dispatch

Times are integer seconds from service-day midnight and can exceed 86400.
`TrainRun` is unique by service date plus train number, never by train number
alone. Weighted routing assigns penalties to service tracks; manual edge paths
provide a reliable override.

An effective timetable is calculated from scheduled stops plus scenario events.
Delay shifts a run; hold shifts the selected station and downstream schedule;
cancel removes only effective occupancy; change-route replaces only the
effective route; assign-track adds a station resource assignment. Recalculate
is explicit and cached results are queried by the UI.
