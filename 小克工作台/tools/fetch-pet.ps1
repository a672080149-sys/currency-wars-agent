# Downloads the Claudia pet animations (kirito1207/claudia-pet, art CC BY-NC-SA 4.0) and converts each
# 8-column WebP frame sheet into a compact PNG sheet (centre-cropped square frames) that 小克工作台 loads from pet\.
param([int]$FrameSize = 240)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore, WindowsBase
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
$pet = Join-Path (Split-Path $PSScriptRoot -Parent) 'pet'
$tmp = Join-Path $pet 'src'
New-Item -ItemType Directory -Force $tmp | Out-Null
$base = 'https://raw.githubusercontent.com/kirito1207/claudia-pet/HEAD/characters/claude/anim/'
$anims = [ordered]@{
  idle     = '待机呼吸休闲'
  thinking = '工作状态-思考冒泡'
  writing  = '写代码'
  running  = '工作状态-忙碌点按'
  happy    = '工作状态-雀跃庆祝'
  oops     = '工作状态-垂头叹气冒汗'
}
$cellW = 640; $cellH = 360; $cols = 8
foreach ($name in $anims.Keys) {
  $webp = Join-Path $tmp "$name.webp"
  # raw.githubusercontent.com drops connections now and then, so retry and check the file is complete
  # (a RIFF/WebP file states its own length in bytes 4-7).
  for ($try = 1; ; $try++) {
    if (Test-Path $webp) {
      $bytes = [IO.File]::ReadAllBytes($webp)
      if ($bytes.Length -gt 12 -and [BitConverter]::ToUInt32($bytes, 4) + 8 -eq $bytes.Length) { break }
      Remove-Item $webp
    }
    if ($try -gt 8) { throw "$name : download failed" }
    & curl.exe -sL --max-time 180 -o $webp ($base + [Uri]::EscapeDataString($anims[$name] + '.webp'))
    if ($LASTEXITCODE -ne 0) { Start-Sleep -Seconds (2 * $try) }
  }
  $s = [IO.File]::OpenRead($webp)
  $src = [Windows.Media.Imaging.BitmapDecoder]::Create($s, 'PreservePixelFormat', 'OnLoad').Frames[0]
  $s.Close()
  # The sheets keep a green colour under fully transparent pixels; converting to premultiplied alpha drops it.
  $src = New-Object Windows.Media.Imaging.FormatConvertedBitmap ($src, [Windows.Media.PixelFormats]::Pbgra32, $null, 0)
  if ($src.PixelWidth -ne $cellW * $cols -or $src.PixelHeight % $cellH -ne 0) { throw "$name : unexpected sheet size $($src.PixelWidth)x$($src.PixelHeight)" }
  $rows = $src.PixelHeight / $cellH
  $visual = New-Object Windows.Media.DrawingVisual
  [Windows.Media.RenderOptions]::SetBitmapScalingMode($visual, 'HighQuality')
  $dc = $visual.RenderOpen()
  for ($r = 0; $r -lt $rows; $r++) {
    for ($c = 0; $c -lt $cols; $c++) {
      $crop = New-Object Windows.Int32Rect (($c * $cellW + ($cellW - $cellH) / 2), ($r * $cellH), $cellH, $cellH)
      $frame = New-Object Windows.Media.Imaging.CroppedBitmap ($src, $crop)
      $dc.DrawImage($frame, (New-Object Windows.Rect (($c * $FrameSize), ($r * $FrameSize), $FrameSize, $FrameSize)))
    }
  }
  $dc.Close()
  $out = New-Object Windows.Media.Imaging.RenderTargetBitmap (($cols * $FrameSize), ($rows * $FrameSize), 96, 96, [Windows.Media.PixelFormats]::Pbgra32)
  $out.Render($visual)
  $enc = New-Object Windows.Media.Imaging.PngBitmapEncoder
  $enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($out))
  $png = Join-Path $pet "$name.png"
  $o = [IO.File]::Create($png); $enc.Save($o); $o.Close()
  "{0,-9} {1} frames -> {2:N0} KB" -f $name, ($rows * $cols), ((Get-Item $png).Length / 1KB)
}
Remove-Item $tmp -Recurse -Force