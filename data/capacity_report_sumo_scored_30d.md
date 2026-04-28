# Sumo Production Capacity Report

Generated UTC: 2026-04-27T12:00:00+00:00
Source database: C:\capacity-intelligence-agentic\data\sumo_live_30d_run.db
Analysis database: C:\capacity-intelligence-agentic\data\sumo_live_scored_report_30d.db

## Run Summary
- Source metrics loaded: 908534
- Analysis queued resources: 482

## Production Recommendation Mix
- hold: 228
- insufficient_data: 187
- watchlist: 64
- total production recommendations: 479

## Production Risk Mix
- high: 187
- low: 135
- medium: 157

## Portfolio Summary
Weekly capacity review generated 482 recommendations on 2026-04-27. 0 scale-down candidate(s) represent about $0.00 in monthly savings, 64 workload(s) remain on watch, and 190 workload(s) need fresher data.

## Top Risk Hotspots
- accionaenergia-orm-blue-ba418b0d2024061211300758090000000e: watchlist, confidence=medium, risk=low
  Evidence: CPU p95 is 6.4%.; Composite pressure score is 45.0 (moderate).; Database connection p95 is 7.4.
- adura-orm-ovp-green-pv: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 24.9%.; Composite pressure score is 0.0 (low).; Primary source freshness is 6.0 hours.
- airproducts-nl-orm-blue-e0db200020230713211209202900000001: watchlist, confidence=medium, risk=low
  Evidence: CPU p95 is 15.5%.; Composite pressure score is 40.8 (moderate).; Database connection p95 is 36.0.
- astrazeneca-orm-blue-8a35b3a62024041813400604150000000e: watchlist, confidence=medium, risk=low
  Evidence: CPU p95 is 6.7%.; Composite pressure score is 45.0 (moderate).; Database connection p95 is 6.1.
- atlanticlng-blue-1ae6978720210611094035176100000004: watchlist, confidence=medium, risk=low
  Evidence: CPU p95 is 68.2%.; Composite pressure score is 46.3 (moderate).; Database connection p95 is 49.1.
- atlanticlng-ovp-green-pvps: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 79.2%.; Composite pressure score is 71.1 (elevated).; Primary source freshness is 6.0 hours.
- azule-energy-blue-af20befd2022120510003778660000000d: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 12.6%.; Composite pressure score is 44.1 (moderate).; Database connection p95 is 80.2.
- bp-blue-105ccddb20210330123154562000000005: watchlist, confidence=medium, risk=low
  Evidence: CPU p95 is 33.7%.; Composite pressure score is 55.1 (elevated).; Database connection p95 is 209.1.
- bsp-orm-blue-5d8f807420251119085920640400000013: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 20.1%.; Composite pressure score is 60.4 (elevated).; Database connection p95 is 128.0.
- bsp-orm-ovp-green-pv: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 100.0%.; Composite pressure score is 100.0 (high).; Primary source freshness is 6.0 hours.
- bwoffshore-orm-blue-4951fefd2024050608315331370000000e: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 20.6%.; Composite pressure score is 64.3 (elevated).; Database connection p95 is 124.3.
- cepsa-orm-blue-e05241bf2023092107474104270000000e: watchlist, confidence=medium, risk=low
  Evidence: CPU p95 is 4.5%.; Composite pressure score is 30.0 (moderate).; Database connection p95 is 5.7.
- chevron-blue-78a7784020221203085622089600000002: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 22.2%.; Composite pressure score is 34.1 (moderate).; Database connection p95 is 80.4.
- chevron-ovp-blue-pv: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 88.7%.; Composite pressure score is 100.0 (high).; Primary source freshness is 6.0 hours.
- chevron-ovp-blue-pvp: watchlist, confidence=medium, risk=medium
  Evidence: CPU p95 is 56.4%.; Composite pressure score is 0.0 (low).; EWMA/CUSUM detected 1 anomaly window(s) with severity 100.0.

## Scale Down Candidates

## Insufficient Data Examples
- app/abertis-orm-OneVisionPackage/0baece1c637606bd: app/abertis-orm-OneVisionPackage/0baece1c637606bd needs fresh data before capacity advice can be trusted.
- app/accionaenergia-orm-OneVisio-bc09/7f5d2a352b26c408: app/accionaenergia-orm-OneVisio-bc09/7f5d2a352b26c408 needs fresh data before capacity advice can be trusted.
- app/adura-orm-OneVisionPackage/26dfe27b8a7ef856: app/adura-orm-OneVisionPackage/26dfe27b8a7ef856 needs fresh data before capacity advice can be trusted.
- app/advansix-orm-OneVisionPackage/3fc0cf216b6e1b68: app/advansix-orm-OneVisionPackage/3fc0cf216b6e1b68 needs fresh data before capacity advice can be trusted.
- app/agl-orm-OneVisionPackage/5d1514276f795eaa: app/agl-orm-OneVisionPackage/5d1514276f795eaa needs fresh data before capacity advice can be trusted.
- app/airproducts-nl-orm-OneVisio-a209/14f5cf728fed55d2: app/airproducts-nl-orm-OneVisio-a209/14f5cf728fed55d2 needs fresh data before capacity advice can be trusted.
- app/airproductsfieldops-OneVisi-193b/8b70df258f2011d2: app/airproductsfieldops-OneVisi-193b/8b70df258f2011d2 needs fresh data before capacity advice can be trusted.
- app/albemarle-orm-OneVisionPackage/448a2f9923860680: app/albemarle-orm-OneVisionPackage/448a2f9923860680 needs fresh data before capacity advice can be trusted.
- app/americantowers-orm-OneVisio-a5cc/3862853398f0ddd6: app/americantowers-orm-OneVisio-a5cc/3862853398f0ddd6 needs fresh data before capacity advice can be trusted.
- app/amtrak-orm-OneVisionPackage/895fa968663e6a87: app/amtrak-orm-OneVisionPackage/895fa968663e6a87 needs fresh data before capacity advice can be trusted.
- app/astrazeneca-orm-OneVisionPackage/a8cb290add090461: app/astrazeneca-orm-OneVisionPackage/a8cb290add090461 needs fresh data before capacity advice can be trusted.
- app/atlanticlng-OneVisionPackage/97e2d69d03648696: app/atlanticlng-OneVisionPackage/97e2d69d03648696 needs fresh data before capacity advice can be trusted.
- app/attero-orm-OneVisionPackage/7dcf41e7445942d2: app/attero-orm-OneVisionPackage/7dcf41e7445942d2 needs fresh data before capacity advice can be trusted.
- app/azule-energy-OneVisionPackage/26442fbe5e481e78: app/azule-energy-OneVisionPackage/26442fbe5e481e78 needs fresh data before capacity advice can be trusted.
- app/azule-ngc-orm-OneVisionPackage/59c38d78bb804d99: app/azule-ngc-orm-OneVisionPackage/59c38d78bb804d99 needs fresh data before capacity advice can be trusted.