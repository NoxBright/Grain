# Speaks a test command with Windows' built-in voice into a 16 kHz mono WAV for Whistle.
param([string]$Text = "walk forward three steps then switch to tripod gait", [string]$Out = "test_clip.wav")

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile((Join-Path (Get-Location) $Out), $format)
$synth.Speak($Text)
$synth.Dispose()
Write-Output "wrote $Out"
