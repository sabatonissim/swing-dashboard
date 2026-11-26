# v79d (26.11.2026): how the scans did against the S&P 500 over the same 10 trading days (step 3 of the scan plan, ~16.12).
# Reads /api/backtest (one request to our server) and daily prices from Yahoo (from home, ~330 requests, ~2 minutes).
# Run: powershell -ExecutionPolicy Bypass -File tests\local_check\scan_measure.ps1
# Nothing is written anywhere; the numbers are printed. Results of 26.11: docs/HISTORY.md section 27.3 tsadi-gimel.
$ErrorActionPreference='Continue'
$h=@{'User-Agent'='swing-check'}
$r=Invoke-WebRequest -UseBasicParsing -Headers $h "https://swing-dashboard-3btx.onrender.com/api/backtest?pattern=all&horizon=10"
$sig=([Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())|ConvertFrom-Json).signals
function Get-Closes($sym){
  try { $y=Invoke-RestMethod -Headers @{'User-Agent'='Mozilla/5.0'} "https://query1.finance.yahoo.com/v8/finance/chart/$($sym)?range=6mo&interval=1d" -TimeoutSec 15 } catch { return $null }
  $res=$y.chart.result[0]; $o=[ordered]@{}
  for($i=0;$i -lt $res.timestamp.Count;$i++){ $c=$res.indicators.quote[0].close[$i]; if($c -ne $null){ $o[[DateTimeOffset]::FromUnixTimeSeconds($res.timestamp[$i]).UtcDateTime.ToString('yyyy-MM-dd')]=[double]$c } }
  return $o
}
$spy=Get-Closes 'SPY'; $days=@($spy.Keys); $idx=@{}; for($i=0;$i -lt $days.Count;$i++){ $idx[$days[$i]]=$i }
$px=@{}; $n=0
foreach($t in ($sig | select -ExpandProperty ticker -Unique)){ $px[$t]=Get-Closes ($t -replace '\.','-'); $n++ }
"tickers fetched: $n"
$rows=@()
foreach($s in $sig){
  $p=$px[$s.ticker]; if(-not $p -or -not $idx.ContainsKey($s.date)){ continue }
  $i=$idx[$s.date]; if($i+10 -ge $days.Count -or $i -lt 63){ continue }
  $d0=$days[$i]; $d10=$days[$i+10]; $dm10=$days[$i-10]
  if(-not ($p.Contains($d0) -and $p.Contains($d10) -and $p.Contains($dm10))){ continue }
  $spyF=($spy[$d10]/$spy[$d0]-1)*100
  $fwd=($p[$d10]/$p[$d0]-1)*100          # close to close, same days as the S&P
  $rel=$fwd-$spyF
  $back10=(($p[$d0]/$p[$dm10])-($spy[$d0]/$spy[$dm10]))*100     # last 10 days, against the S&P
  $win=$days[($i-62)..$i]
  $hiS=($win|%{$spy[$_]}|Measure-Object -Maximum).Maximum; $ddS=($spy[$d0]/$hiS-1)*100
  $hiP=($win|?{$p.Contains($_)}|%{$p[$_]}|Measure-Object -Maximum).Maximum; $ddP=($p[$d0]/$hiP-1)*100
  $rows+=[pscustomobject]@{date=$s.date;t=$s.ticker;pat=$s.pattern_type;rs=[int]$s.rs_rating;rel=$rel;back10=$back10;ddgap=$ddP-$ddS;ddP=$ddP}
}
function Show($name,$grp){ "== $name"; $grp | sort Name | %{ $g=$_.Group; $c=$g.Count; "{0,-36} n={1,4}  vs S&P avg {2,6:N2}%  beat S&P {3,5:N1}%" -f $_.Name,$c,($g|Measure-Object rel -Average).Average,(($g|?{$_.rel -gt 0}).Count/$c*100) } }
"rows: $($rows.Count)"
Show 'all' ($rows|group {'all'})
Show 'last 10 days vs S&P' ($rows|group { if($_.back10 -ge 5){'a 5%+ better'}elseif($_.back10 -ge 0){'b 0-5% better'}elseif($_.back10 -ge -5){'c 0-5% worse'}else{'d 5%+ worse'} })
Show 'drop from 3-month high vs the S&Ps drop' ($rows|group { if($_.ddgap -ge 0){'a held up better than S&P'}elseif($_.ddgap -ge -5){'b up to 5 pts worse'}elseif($_.ddgap -ge -15){'c 5-15 pts worse'}else{'d 15+ pts worse'} })
Show 'distance from own 3-month high' ($rows|group { if($_.ddP -ge -5){'a within 5%'}elseif($_.ddP -ge -15){'b 5-15% below'}else{'c 15%+ below'} })
Show 'RS 80+ and held up' ($rows|group { "rs80=" + ($_.rs -ge 80) + " held=" + ($_.ddgap -ge -5) })
Show 'ma150 bounce by held up' ($rows|?{$_.pat -like 'ma150*'}|group { "held=" + ($_.ddgap -ge -5) })

