
param(
	[string]$TargetSsid = "YellowToyCar",
	[string]$TargetIp = "192.168.4.1",
	[int]$CheckIntervalMilliseconds = 333,
	[int]$ReconnectIntervalMilliseconds = 500,
	[ValidateSet("OnlyInterface", "OnlyScan", "Both")]
	[string]$SignalStrengthSource = "OnlyScan",
	[switch]$ShowRemoteSignal = $false
)

function Get-SignalColor {
	param(
		[int]$Value,
		[ValidateSet("percent", "dbm", "ping")]
		[string]$Type
	)

	if ($Type -eq "percent") {
		if ($Value -gt 90) { return "DarkGreen" }
		if ($Value -gt 80) { return "Green" }
		if ($Value -gt 70) { return "Yellow" }
		if ($Value -gt 60) { return "DarkYellow" }
		if ($Value -gt 50) { return "DarkRed" }
		return "Red"
	}
	elseif ($Type -eq "dbm") {
		if ($Value -gt -40) { return "DarkGreen" }
		if ($Value -gt -50) { return "Green" }
		if ($Value -gt -60) { return "Yellow" }
		if ($Value -gt -70) { return "DarkYellow" }
		if ($Value -gt -80) { return "DarkRed" }
		return "Red"
	}
	elseif ($Type -eq "ping") {
		if ($Value -lt 30) { return "DarkGreen" }
		if ($Value -lt 80) { return "Green" }
		if ($Value -lt 150) { return "Yellow" }
		if ($Value -lt 300) { return "DarkYellow" }
		if ($Value -lt 500) { return "DarkRed" }
		return "Red"
	}
}

function Get-LogLineDate {
	return "[$(Get-Date -Format 'HH:mm:ss.fff')]"
}

Write-Host "Starting Wi-Fi auto-connect for SSID: '$TargetSsid'"
Write-Host "Press CTRL+C to stop."

$firstPingTimeoutTimestamp = $null

while ($true) {
	try {
		# Get the current Wi-Fi connection details
		$interfaces = netsh wlan show interfaces
		$connection = $interfaces | Select-String -Pattern "^\s+SSID\s+:\s(.+)"

		# Check if connected and if the SSID matches the target
		if ($connection -and ($connection.Matches[0].Groups[1].Value.Trim() -eq $TargetSsid)) {
			$signalFromInterface = $null
			if ($SignalStrengthSource -in @("OnlyInterface", "Both")) {
				# Extracting signal strength; not updating quite fast, only every 60 seconds I think (see https://github.com/microsoft/Windows-Dev-Performance/issues/59)
				$signal = $interfaces | Select-String -Pattern "^\s+Signal\s+:\s(.+)"
				$signalFromInterface = if ($signal) { $signal.Matches[0].Groups[1].Value.Trim() } else { "N/A" }
			}

			$signalFromNetworks = $null
			if ($SignalStrengthSource -in @("OnlyScan", "Both")) {
				# Following will maybe force scan
				$scan = (netsh wlan show networks mode=bssid | Out-String) -split "(?m)(?=^SSID\s+\d+\s*:)" `
					| Select-Object -Skip 1 |  Where-Object { $_.Split("`n")[0].Split(":")[1].Trim() -eq $TargetSsid } | ForEach-Object { $_.Split("`n") } 
				$signalFromNetworks = $scan | Where-Object { $_.Split(":")[0].Trim() -eq 'Signal' } | ForEach-Object { $_.Split(":")[1].Trim() }
			}

			# Print, ending with ping 
			Write-Host -NoNewline "$(Get-LogLineDate) "

			if ($SignalStrengthSource -in @("OnlyInterface", "Both") -and $signalFromInterface) {
				$percentValue = [int]($signalFromInterface -replace '%', '')
				Write-Host -NoNewline "Signal: "
				Write-Host -NoNewline "$signalFromInterface" -ForegroundColor (Get-SignalColor -Value $percentValue -Type "percent")
				if ($SignalStrengthSource -eq "Both") { Write-Host -NoNewline " (if.)" }
			}
			if ($SignalStrengthSource -in @("OnlyScan", "Both") -and $signalFromNetworks) {
				if ($SignalStrengthSource -eq "Both") { Write-Host -NoNewline " or " } else { Write-Host -NoNewline "Signal: " }
				$percentValue = [int]($signalFromNetworks -replace '%', '')
				Write-Host -NoNewline "$signalFromNetworks" -ForegroundColor (Get-SignalColor -Value $percentValue -Type "percent")
				if ($SignalStrengthSource -eq "Both") { Write-Host -NoNewline " (scan)" }
			}
			Write-Host -NoNewline " | "

			Write-Host -NoNewline "ICMP: "
			$pingResult = Test-Connection -ComputerName $TargetIp -Count 1 -TimeoutSeconds 1 -ErrorAction SilentlyContinue
			if ($pingResult -and $pingResult.Status -eq 'Success') {
				$firstPingTimeoutTimestamp = $null
				$pingColor = Get-SignalColor -Value $pingResult.Latency -Type "ping"
				Write-Host -NoNewline "$($pingResult.Latency)ms".PadLeft(5) -ForegroundColor $pingColor
				Start-Sleep -Milliseconds $CheckIntervalMilliseconds
			}
			else {
				Write-Host -NoNewline "Timeout" -ForegroundColor Red

				if (-not $firstPingTimeoutTimestamp) {
					$firstPingTimeoutTimestamp = Get-Date
				}
				# Try disconnect when signal stuck while ping timeouts keep happening
				if (((Get-Date) - $firstPingTimeoutTimestamp).TotalSeconds -ge 3) {
					Write-Host -NoNewline " | Disconnecting to try refresh..."
					netsh wlan disconnect | Out-Host
					$firstPingTimeoutTimestamp = $null
					continue
				}
			}

			if ($ShowRemoteSignal) {
				Write-Host -NoNewline " | HTTP: "
				if ($pingResult.Status -eq 'Success') {
					try {
						$statusRequestMeasurement = Measure-Command {
							$statusResponse = Invoke-WebRequest -Uri "http://$TargetIp/status?details=1" -TimeoutSec 2 -UseBasicParsing
						}
						$statusRequestMs = [int]$statusRequestMeasurement.TotalMilliseconds
						Write-Host -NoNewline "$($statusRequestMs)ms".PadLeft(5) -ForegroundColor (Get-SignalColor -Value $statusRequestMs -Type "ping")

						$localMac = ($interfaces | Select-String -Pattern "^\s+Physical address\s+:\s(.+)").Matches[0].Groups[1].Value.Trim().Replace("-", "").Replace(":", "").ToLower()
						$status = $statusResponse.Content | ConvertFrom-Json
						$remoteStation = $status.stations | Where-Object { ($_.mac.Replace("-", "").Replace(":", "").ToLower()) -eq $localMac }
						if ($remoteStation) {
							$remoteRssi = $remoteStation.rssi
							Write-Host -NoNewline " | RSSI: "
							Write-Host -NoNewline "${remoteRssi}dBm" -ForegroundColor (Get-SignalColor -Value $remoteRssi -Type "dbm")
						}
						else {
							Write-Host -NoNewline "Missing" -ForegroundColor "DarkGray"
						}
					}
					catch {
						Write-Host -NoNewline "Timeout" -ForegroundColor "Red"
					}
				}
				else { # Ping failed
					Write-Host -NoNewline "Skipped"
				}
			}

			Write-Host " " # for the new line
		}
		else { # not connected
			$firstPingTimeoutTimestamp = $null
			Write-Host -NoNewline "$(Get-LogLineDate) Not connected to '$TargetSsid'. Attempting to connect... "
			netsh wlan connect name="$TargetSsid" | Out-Host
			Start-Sleep -Milliseconds $ReconnectIntervalMilliseconds
		}
	}
	catch {
		Write-Warning "$(Get-LogLineDate) An error occurred: $_"
		Start-Sleep -Milliseconds 1000
	}
}
