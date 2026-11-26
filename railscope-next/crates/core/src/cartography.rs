//! Derive display facts from the immutable OSM source. Never use these to route a train.
use serde::Serialize;
use serde_json::Value;

#[derive(Serialize)]
pub struct Semantics {
    pub render_class: &'static str,
    pub lifecycle: &'static str,
    pub reason: &'static str,
    pub speed_kmh: Option<f64>,
    pub gauge_mm: Option<u32>,
    pub electrification: Option<&'static str>,
    pub business: Option<&'static str>,
    pub technical: Option<&'static str>,
    pub grade: Option<f64>,
}

fn limit(value: &str) -> Option<f64> {
    let (number, factor) = match value.split_once(' ') {
        Some((number, "mph")) => (number, 1.609344),
        Some((number, "km/h" | "kmh" | "kph")) => (number, 1.0),
        None => (value, 1.0),
        _ => return None,
    };
    let number = number.parse::<f64>().ok()? * factor;
    (number.is_finite() && number > 0.0).then_some(number)
}

pub fn classify(
    tags: &Value,
    kind: &str,
    legacy_class: &str,
    status: &str,
    verified: bool,
) -> Semantics {
    let tag = |key: &str| tags.get(key).and_then(Value::as_str).unwrap_or("");
    let raw = tag("railway");
    let states = ["construction", "proposed", "disused", "abandoned", "razed"];
    let mut markings = states
        .into_iter()
        .filter(|state| raw == *state || !tag(&format!("{state}:railway")).is_empty());
    let marking = markings.next();
    let conflict = markings.next().is_some();
    let lifecycle = if conflict {
        "unknown"
    } else if let Some(value) = marking {
        value
    } else {
        match status {
            "operating" => "active",
            "construction" => "construction",
            "planned" => "proposed",
            "disused" => "disused",
            _ => "unknown",
        }
    };
    let mode = if let Some(key) = marking {
        let prefixed = tag(&format!("{key}:railway"));
        if !prefixed.is_empty() {
            prefixed
        } else if raw == key {
            tag(key)
        } else {
            raw
        }
    } else {
        raw
    };
    let (render_class, reason) = match kind {
        "rail" | "metro" => match (conflict, tag("service"), mode) {
            (true, _, _) => ("unknown_track", "多个生命周期标记冲突"),
            (_, "siding", _) => ("track_siding", "OSM service=siding"),
            (_, "yard", _) => ("track_yard", "OSM service=yard"),
            (_, "crossover", _) => ("track_crossover", "OSM service=crossover"),
            (_, "spur", _) => ("track_spur", "OSM service=spur"),
            (_, "", "rail" | "narrow_gauge") => match (tag("highspeed"), tag("usage")) {
                ("yes", "" | "main") => ("rail_highspeed", "OSM highspeed=yes，需独立核验"),
                ("yes", _) => ("unknown_track", "highspeed 与线路用途需核查"),
                (_, "main") => ("rail_main", "OSM usage=main"),
                (_, "branch") => ("rail_branch", "OSM usage=branch"),
                _ => ("rail_unspecified", "缺少线路等级证据"),
            },
            (_, "", "subway") => ("metro_track", "OSM railway=subway"),
            (_, "", "light_rail") => ("light_rail_track", "OSM railway=light_rail"),
            (_, "", "tram") => ("tram_track", "OSM railway=tram"),
            (_, "", "monorail" | "maglev") => ("monorail_track", "OSM 轨道制式"),
            _ => ("unknown_track", "制式或轨道用途未分类"),
        },
        "station" | "facility" => match (raw, legacy_class) {
            ("yard", _) => ("rail_yard_site", "OSM railway=yard"),
            ("junction", _) => ("rail_junction_site", "OSM railway=junction"),
            ("crossover", _) => ("rail_crossover_site", "OSM railway=crossover"),
            ("spur_junction", _) => ("rail_spur_junction_site", "OSM railway=spur_junction"),
            ("halt", _) => ("rail_halt", "OSM railway=halt"),
            ("tram_stop", _) => ("tram_stop", "OSM railway=tram_stop"),
            ("switch", _) => ("switch_detail", "OSM railway=switch"),
            ("railway_crossing", _) => ("railway_crossing_detail", "OSM railway=railway_crossing"),
            ("signal", _) => ("signal_detail", "OSM railway=signal"),
            ("subway_entrance", _) => ("subway_entrance", "OSM railway=subway_entrance"),
            ("platform", _) | (_, "platform") => ("platform", "保留源几何类型"),
            (_, "station_building") => ("station_building", "来源标注为站房建筑"),
            (_, "railway_land") | (_, "station_area") if tag("landuse") == "railway" => {
                ("railway_land", "普通铁路用地")
            }
            (_, "station_area") => ("station_area", "来源标注为站区"),
            (_, "metro_station") if verified && tag("transfer_verified") == "yes" => {
                ("metro_interchange", "已核验实际换乘关系")
            }
            (_, "metro_station") => ("metro_station", "OSM 城市轨道站"),
            (_, "rail_station") if raw != "station" && tag("train") != "yes" => {
                if tag("bus") == "yes"
                    || tag("highway") == "bus_stop"
                    || tag("amenity") == "bus_station"
                {
                    ("bus_station", "OSM 公交场站或站点")
                } else {
                    ("unclassified_transport_station", "缺少铁路车站来源证据")
                }
            }
            (_, "rail_station") if tag("passenger") == "yes" => {
                ("rail_station_passenger", "OSM passenger=yes")
            }
            (_, "rail_station") => ("rail_station_unknown", "客运业务未确认"),
            _ => ("rail_site_unknown", "未分类铁路设施"),
        },
        _ => ("", "沿用源分类"),
    };
    let business = match (tag("passenger"), tag("freight")) {
        ("yes", "yes") => Some("mixed"),
        ("yes", _) => Some("passenger"),
        ("no" | "", "yes" | "only") => Some("freight"),
        _ => None,
    };
    let technical = if verified {
        match tag("technical_class") {
            "marshalling" => Some("marshalling"),
            "district" => Some("district"),
            "intermediate" => Some("intermediate"),
            _ => None,
        }
    } else {
        None
    };
    let grade = if verified {
        match tag("verified_grade") {
            "0" => Some(1.30),
            "1" => Some(1.20),
            "2" => Some(1.10),
            "3" => Some(1.00),
            "4" => Some(0.90),
            "5" => Some(0.85),
            _ => None,
        }
    } else {
        None
    };
    Semantics {
        render_class,
        lifecycle,
        reason,
        speed_kmh: limit(tag("maxspeed")),
        gauge_mm: tag("gauge").parse::<u32>().ok().filter(|v| *v > 0),
        electrification: match tag("electrified") {
            "contact_line" => Some("overhead"),
            "rail" => Some("third_rail"),
            "no" => Some("none"),
            _ => None,
        },
        business,
        technical,
        grade,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn derives_track_types_and_lifecycle_independently() {
        for (tags, expected) in [
            (json!({"railway":"rail", "usage":"main"}), "rail_main"),
            (
                json!({"railway":"rail", "maxspeed":"350", "name":"某高铁"}),
                "rail_unspecified",
            ),
            (
                json!({"railway":"rail", "highspeed":"yes"}),
                "rail_highspeed",
            ),
            (
                json!({"railway":"rail", "highspeed":"yes", "service":"siding"}),
                "track_siding",
            ),
            (
                json!({"railway":"rail", "service":"crossover"}),
                "track_crossover",
            ),
            (
                json!({"railway":"rail", "usage":"main", "service":"unknown"}),
                "unknown_track",
            ),
        ] {
            assert_eq!(
                classify(&tags, "rail", "", "operating", false).render_class,
                expected
            );
        }
        let x = classify(
            &json!({"abandoned:railway":"rail"}),
            "rail",
            "",
            "disused",
            false,
        );
        assert_eq!(x.lifecycle, "abandoned");
        assert!(x.speed_kmh.is_none());
        assert!((limit("55 mph").unwrap() - 88.51392).abs() < 1e-9);
        assert!(limit("80;100").is_none());
    }

    #[test]
    fn station_evidence_does_not_follow_track_purpose() {
        for (tags, kind, class, output) in [
            (
                json!({"railway":"station", "railway:station_category":"CN:0"}),
                "station",
                "rail_station",
                "rail_station_unknown",
            ),
            (
                json!({"railway":"station", "passenger":"yes"}),
                "station",
                "rail_station",
                "rail_station_passenger",
            ),
            (
                json!({"railway":"halt"}),
                "station",
                "rail_station",
                "rail_halt",
            ),
            (
                json!({"public_transport":"station","bus":"yes","name":"龙江小区"}),
                "station",
                "rail_station",
                "bus_station",
            ),
            (
                json!({"public_transport":"station"}),
                "station",
                "rail_station",
                "unclassified_transport_station",
            ),
            (
                json!({"public_transport":"station","bus":"yes","train":"yes"}),
                "station",
                "rail_station",
                "rail_station_unknown",
            ),
            (
                json!({"railway":"crossover"}),
                "facility",
                "crossover",
                "rail_crossover_site",
            ),
            (
                json!({"railway":"yard"}),
                "facility",
                "yard",
                "rail_yard_site",
            ),
            (
                json!({"railway":"platform"}),
                "facility",
                "platform",
                "platform",
            ),
            (
                json!({"building":"train_station"}),
                "facility",
                "station_building",
                "station_building",
            ),
            (
                json!({"landuse":"railway"}),
                "facility",
                "station_area",
                "railway_land",
            ),
        ] {
            let result = classify(&tags, kind, class, "unknown", false);
            assert_eq!(result.render_class, output);
            assert!(result.grade.is_none());
        }
        let tags = json!({"station":"subway","transfer_verified":"yes"});
        assert_eq!(
            classify(&tags, "station", "metro_station", "unknown", true).render_class,
            "metro_interchange"
        );
    }
}
