
param(
	[string]$TargetSsid = "YellowToyCar",
	[string]$TargetIp = "192.168.4.1",
	[int]$CheckIntervalMilliseconds = 333,
	[ValidateSet("OnlyInterface", "OnlyScan", "Both")]
	[string]$SignalStrengthSource = "OnlyScan"
)

Write-Host "Starting Wi-Fi auto-connect for SSID: '$TargetSsid'"
Write-Host "Press CTRL+C to stop."

while ($true) {
	try {
		# Get the current Wi-Fi connection details
		$interfaces = netsh wlan show interfaces
		$connection = $interfaces | Select-String -Pattern "^\s+SSID\s+:\s(.+)"

		# Check if connected and if the SSID matches the target
		if ($connection -and ($connection.Matches[0].Groups[1].Value.Trim() -eq $TargetSsid)) {
			$signalFromInterface = "N/A"
			if ($SignalStrengthSource -in @("OnlyInterface", "Both")) {
				# Extracting signal strength; not updating quite fast, only every 60 seconds I think (see https://github.com/microsoft/Windows-Dev-Performance/issues/59)
				$signal = $interfaces | Select-String -Pattern "^\s+Signal\s+:\s(.+)"
				$signalFromInterface = if ($signal) { $signal.Matches[0].Groups[1].Value.Trim() } else { "N/A" }
			}

			$signalFromNetworks = "N/A"
			if ($SignalStrengthSource -in @("OnlyScan", "Both")) {
				# Following will maybe force scan
				$scan = (netsh wlan show networks mode=bssid | Out-String) -split "(?m)(?=^SSID\s+\d+\s*:)" `
					| Select-Object -Skip 1 |  Where-Object { $_.Split("`n")[0].Split(":")[1].Trim() -eq $TargetSsid } | ForEach-Object { $_.Split("`n") } 
				$signalFromNetworks = $scan | Where-Object { $_.Split(":")[0].Trim() -eq 'Signal' } | ForEach-Object { $_.Split(":")[1].Trim() }
			}

			$signalText = switch ($SignalStrengthSource) {
				"OnlyInterface" { "Signal: $signalFromInterface." }
				"OnlyScan"      { "Signal: $signalFromNetworks." }
				"Both"          { "Signal: $signalFromInterface (if.) or $signalFromNetworks (scan)." }
			}

			# Print, ending with ping 
			Write-Host -NoNewline "$(Get-Date -Format 'HH:mm:ss.fff') - Connected to '$TargetSsid'. $signalText Pinging $TargetIp... "
			$pingResult = Test-Connection -ComputerName $TargetIp -Count 1 -ErrorAction SilentlyContinue
			if ($pingResult -and $pingResult.Status -eq 'Success') {
				Write-Host "OK ($($pingResult.Latency)ms)" -ForegroundColor Green
				Start-Sleep -Milliseconds $CheckIntervalMilliseconds
			} else {
				Write-Host "Failed" -ForegroundColor Red
			}
		} else {
			Write-Host -NoNewline "$(Get-Date -Format 'HH:mm:ss.fff') - Not connected to '$TargetSsid'. Attempting to connect... "
			netsh wlan connect name="$TargetSsid" | Out-Host
		}
	}
	catch {
		Write-Warning "$(Get-Date -Format 'HH:mm:ss.fff') - An error occurred: $_"
	}
	Start-Sleep -Milliseconds $CheckIntervalMilliseconds
}
