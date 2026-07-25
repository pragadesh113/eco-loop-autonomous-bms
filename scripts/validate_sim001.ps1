$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$weather = Join-Path $projectRoot 'weather\IND_DL_New.Delhi-Safdarjung.AP.421820_TMYx.2011-2025.epw'
$energyPlusMatches = @(
    Get-ChildItem -Recurse -File (Join-Path $projectRoot '.tools\energyplus\26.1.0') `
        -Filter 'energyplus.exe'
)

if ($energyPlusMatches.Count -ne 1) {
    throw "Expected exactly one EnergyPlus 26.1.0 executable; found $($energyPlusMatches.Count)."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python is missing: $python"
}
if (-not (Test-Path -LiteralPath $weather -PathType Leaf)) {
    throw "New Delhi EPW is missing: $weather"
}

& $python -m bms_agent.simulation.model_prep
if ($LASTEXITCODE -ne 0) {
    throw "Model preparation failed with exit code $LASTEXITCODE."
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$validationRoot = Join-Path $projectRoot ".cache\validation\sim001\script-$timestamp"
$requiredRddEntries = @(
    'Site Outdoor Air Drybulb Temperature [C]',
    'People Occupant Count []',
    'Zone Thermostat Cooling Setpoint Temperature [C]',
    'Zone Thermal Comfort Fanger Model PMV []',
    'Zone Thermal Comfort Fanger Model PPD [%]',
    'Schedule Value []'
)
$actuatorEntry = (
    'EnergyManagementSystem:Actuator Available,CLG-SETP-SCH,' +
    'Schedule:Compact,Schedule Value,[ ]'
)
$results = @()

foreach ($mode in @('baseline', 'controlled')) {
    $model = Join-Path $projectRoot "models\5ZoneAirCooled.$mode.v1.idf"
    $outputDirectory = Join-Path $validationRoot $mode
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

    & $energyPlusMatches[0].FullName -w $weather -d $outputDirectory $model
    if ($LASTEXITCODE -ne 0) {
        throw "EnergyPlus $mode validation failed with exit code $LASTEXITCODE."
    }

    $endFile = Join-Path $outputDirectory 'eplusout.end'
    $errFile = Join-Path $outputDirectory 'eplusout.err'
    $rddFile = Join-Path $outputDirectory 'eplusout.rdd'
    $mddFile = Join-Path $outputDirectory 'eplusout.mdd'
    $eddFile = Join-Path $outputDirectory 'eplusout.edd'

    if ((Get-Content -Raw $endFile) -notmatch 'Completed Successfully') {
        throw "EnergyPlus $mode run did not report successful completion."
    }
    if (@(Select-String -Path $errFile -Pattern '^\s+\*\* Severe \*\*').Count -gt 0) {
        throw "EnergyPlus $mode run reported a severe error."
    }
    foreach ($entry in $requiredRddEntries) {
        if (@(Select-String -Path $rddFile -SimpleMatch $entry).Count -eq 0) {
            throw "EnergyPlus $mode RDD is missing: $entry"
        }
    }
    if (@(Select-String -Path $mddFile -SimpleMatch 'Electricity:HVAC [J]').Count -eq 0) {
        throw "EnergyPlus $mode MDD is missing Electricity:HVAC."
    }
    if (@(Select-String -Path $eddFile -SimpleMatch $actuatorEntry).Count -ne 1) {
        throw "EnergyPlus $mode EDD does not prove the selected schedule actuator."
    }

    $results += [pscustomobject]@{
        mode = $mode
        model = $model
        outputDirectory = $outputDirectory
        energyPlusExitCode = 0
        severeErrors = 0
        warnings = @(Select-String -Path $errFile -SimpleMatch '** Warning **').Count
        actuator = @{
            key = 'CLG-SETP-SCH'
            componentType = 'Schedule:Compact'
            controlType = 'Schedule Value'
        }
        rddSha256 = (Get-FileHash -Algorithm SHA256 $rddFile).Hash
        mddSha256 = (Get-FileHash -Algorithm SHA256 $mddFile).Hash
        eddSha256 = (Get-FileHash -Algorithm SHA256 $eddFile).Hash
    }
}

[pscustomobject]@{
    feature = 'SIM-001'
    validatedAt = (Get-Date).ToString('o')
    energyPlusVersion = '26.1.0'
    weatherSha256 = (Get-FileHash -Algorithm SHA256 $weather).Hash
    runs = $results
} | ConvertTo-Json -Depth 6
