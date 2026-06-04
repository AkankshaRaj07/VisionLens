param(
    [string]$ApiUrl = "",
    [switch]$FastMode
)

$ErrorActionPreference = "Stop"

$CLIPS_DIR = "data\clips"
$EVENTS_DIR = "data\events"
$LAYOUT = "data\store_layout.json"

if (!(Test-Path $EVENTS_DIR)) {
    New-Item -ItemType Directory -Path $EVENTS_DIR | Out-Null
}

if (!(Test-Path $CLIPS_DIR)) {
    Write-Host "ERROR: $CLIPS_DIR not found. Place your CCTV clips there first." -ForegroundColor Red
    exit 1
}

$stores = Get-ChildItem -Path $CLIPS_DIR -Directory
foreach ($store in $stores) {
    $store_id = $store.Name
    Write-Host "`n═══ Store: $store_id ═══" -ForegroundColor Cyan
    
    $clips = Get-ChildItem -Path $store.FullName -Include *.mp4, *.avi -Recurse
    foreach ($clip in $clips) {
        $filename = $clip.BaseName
        
        if ($filename -match "ENTRY|entry") {
            $camera_type = "entry"
        } elseif ($filename -match "BILLING|billing|BILL") {
            $camera_type = "billing"
        } else {
            $camera_type = "floor"
        }
        
        $output = "$EVENTS_DIR\${store_id}_${filename}.jsonl"
        Write-Host "▶ Processing $($clip.FullName) -> $output"
        
        $args = @(
            "-m", "pipeline.detect",
            "--clip", $clip.FullName,
            "--store-id", $store_id,
            "--camera-id", $filename,
            "--camera-type", $camera_type,
            "--layout", $LAYOUT,
            "--output", $output
        )
        if ($ApiUrl) {
            $args += "--api-url"
            $args += $ApiUrl
        }
        if ($FastMode) {
            $args += "--fast"
        }
        
        & python $args
    }
}

Write-Host "`n✅ All clips processed. Events in $EVENTS_DIR\" -ForegroundColor Green

Write-Host "▶ Correlating POS transactions..." -ForegroundColor Cyan

$PosFiles = Get-ChildItem -Path "data" -Filter "*.csv" | Where-Object { $_.Name -match "pos|brigade" } | Select-Object -First 1
$PosFile = if ($PosFiles) { $PosFiles.FullName } else { "data\pos_transactions.csv" }

if ($ApiUrl) {
    & python pipeline\correlate_pos.py --events-dir $EVENTS_DIR --pos-file "$PosFile" --api-url $ApiUrl
} else {
    & python pipeline\correlate_pos.py --events-dir $EVENTS_DIR --pos-file "$PosFile"
}

if (!$ApiUrl) {
    Write-Host "`nTo ingest events into the API, run:"
    Write-Host "  .\pipeline\run.ps1 -ApiUrl http://localhost:8000"
    Write-Host "`nOr replay events manually:"
    Write-Host "  python pipeline\replay.py --events-dir data\events --api-url http://localhost:8000"
}
