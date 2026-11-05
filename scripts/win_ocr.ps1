param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$OutJson
)

# Windows OCR (Windows.Media.Ocr) — 라인별 text/x/y/right/bottom 을 JSON 으로 출력
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]

$full = (Resolve-Path -LiteralPath $Path).Path

$file    = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
$stream  = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    try {
        $lang = [Windows.Globalization.Language]::new('ko-KR')
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
    } catch { }
}
if ($null -eq $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
        [Windows.Globalization.Language]::new('en-US'))
}
if ($null -eq $engine) {
    @{ content = @(); error = 'no_ocr_engine' } | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $OutJson -Encoding UTF8
    exit 3
}

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$lines = New-Object System.Collections.ArrayList
foreach ($line in $result.Lines) {
    $words = $line.Words
    if ($null -eq $words -or $words.Count -eq 0) { continue }

    $minX = [double]::MaxValue; $minY = [double]::MaxValue
    $maxR = [double]::MinValue; $maxB = [double]::MinValue
    foreach ($w in $words) {
        $rc = $w.BoundingRect
        if ($rc.X -lt $minX) { $minX = $rc.X }
        if ($rc.Y -lt $minY) { $minY = $rc.Y }
        if (($rc.X + $rc.Width)  -gt $maxR) { $maxR = $rc.X + $rc.Width }
        if (($rc.Y + $rc.Height) -gt $maxB) { $maxB = $rc.Y + $rc.Height }
    }

    [void]$lines.Add([pscustomobject]@{
        text   = $line.Text
        x      = [int][math]::Round($minX)
        y      = [int][math]::Round($minY)
        right  = [int][math]::Round($maxR)
        bottom = [int][math]::Round($maxB)
    })
}

$payload = [pscustomobject]@{
    content = @($lines)
    count   = $lines.Count
    engine  = $engine.RecognizerLanguage.LanguageTag
}
$json = $payload | ConvertTo-Json -Depth 5
# BOM 없는 UTF-8 (Python json.load 은 BOM 을 싫어한다)
[System.IO.File]::WriteAllText($OutJson, $json, (New-Object System.Text.UTF8Encoding($false)))
