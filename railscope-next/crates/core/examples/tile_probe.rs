use anyhow::{Context, Result, ensure};
use railscope_next_core::{pack::Pack, precision::Address};
use std::{path::PathBuf, time::Instant};

fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let [pack, longitude, latitude, output, zooms] = args.as_slice() else {
        anyhow::bail!("tile_probe PACK LONGITUDE LATITUDE OUTPUT ZOOMS, e.g. 8,12,15,17,19,21");
    };
    let longitude: f64 = longitude.parse()?;
    let latitude: f64 = latitude.parse()?;
    ensure!(
        (-180.0..180.0).contains(&longitude) && (-85.0..=85.0).contains(&latitude),
        "invalid coordinate"
    );
    let began = Instant::now();
    let pack = Pack::open(&PathBuf::from(pack))?;
    let startup_ms = began.elapsed().as_secs_f64() * 1000.0;
    let output = PathBuf::from(output);
    std::fs::create_dir_all(&output)?;
    let mut results = Vec::new();
    for zoom in zooms.split(',') {
        let z: u8 = zoom.parse()?;
        ensure!(z <= 22, "invalid zoom");
        let scale = f64::from(1u32 << z);
        let x = (((longitude + 180.0) / 360.0) * scale).floor() as u32;
        let y = ((1.0 - latitude.to_radians().tan().asinh() / std::f64::consts::PI) * 0.5 * scale)
            .floor() as u32;
        let bounds = Address::new(z, x, y)?.bounds();
        let stamp = Instant::now();
        let tile = pack.tile(z, x, y).context(format!("render {z}/{x}/{y}"))?;
        let cold_ms = stamp.elapsed().as_secs_f64() * 1000.0;
        let stamp = Instant::now();
        let from_ram = pack.tile(z, x, y)?;
        let ram_ms = stamp.elapsed().as_secs_f64() * 1000.0;
        ensure!(
            tile == from_ram,
            "tile changed between cold and warm retrieval"
        );
        let name = format!("{z}-{x}-{y}.mvt");
        std::fs::write(output.join(&name), &tile)?;
        results.push(serde_json::json!({
            "z":z,"x":x,"y":y,"tile":name,"bounds":bounds,"cold_ms":cold_ms,"ram_ms":ram_ms,"bytes":tile.len(),
            "precision":if pack.is_precise(z){"original_source"}else{"overview"},
        }));
    }
    let report = serde_json::json!({"snapshot":pack.manifest.snapshot,"source_schema":pack.manifest.source_schema,
      "longitude":longitude,"latitude":latitude,"pack_validation_ms":startup_ms,"tiles":results,"stats":pack.stats()});
    std::fs::write(
        output.join("tiles.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
