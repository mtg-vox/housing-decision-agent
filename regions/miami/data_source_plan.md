# Data Source Plan

Use this plan when researching Miami-area housing options. Data must be refreshed at the time of each serious query because rents, inventory, construction, traffic, safety, and flood conditions can change.

## Capability Model

The agent can:
- browse current web pages and cite URLs
- query public web APIs or GIS REST services when accessible without credentials
- use user-provided API keys if the user supplies them and asks for direct API use
- save source snapshots and research notes into your profile folder, not this repo
- cross-check major claims across multiple sources

The agent cannot:
- guarantee complete rental inventory from a single source
- bypass paywalls, login walls, anti-bot protections, or site terms
- use private MLS data unless the user provides lawful access
- run persistent scheduled monitoring unless the user sets up an external job
- guarantee that listing portals are current without confirming with the property manager, building, or listing agent

## Source Tiers

### Tier 1: Official Or Primary Sources

Use these for decision-critical facts when available.

| Topic | Preferred Sources | Query Method | Notes |
|---|---|---|---|
| Flood zones | FEMA Flood Map Service Center and National Flood Hazard Layer | Web + FEMA GIS services | Best official baseline for mapped flood risk. |
| Storm surge / local flood layers | Miami-Dade County GIS and municipal open data where available | Web + ArcGIS REST/open data | Use for Miami-specific local context. |
| Sea level rise and king tides | NOAA Sea Level Rise Viewer, NOAA Tides and Currents, City of Miami King Tides | Web + NOAA data/download services | Useful for coastal and low-elevation areas beyond FEMA flood-zone labels. |
| Air quality | AirNow, EPA AirData/AQS, EPA EJScreen | Web + API where available | AirNow is best for current/forecast AQI; EPA AirData/AQS and EJScreen are better for monitoring and environmental indicators. |
| Traffic incidents and roadway conditions | Florida 511 / FDOT | Web, map, alerts, public endpoints if available | Good for current incidents, construction, travel times, cameras, and major roads. |
| Traffic volume / road exposure | FDOT Traffic Information / Florida Traffic Online | Web + downloadable files where available | Useful for air/noise exposure near major corridors. |
| Road/bridge construction | FDOT District Six, FL511, City/County transportation pages | Web + project pages | Needed for Brickell/downtown/bridge-friction decisions. |
| City public works and flooding complaints | City of Miami Open Data, City of Miami 311, FloodTracker, RPW Project Map | Web + open-data/API where available | Useful for block-level flooding, potholes, drainage, and project-disruption signals. |
| Building permits and development | City of Miami Building/iBuild, Miami-Dade permitting, Miami Beach permit search, municipal open data | Web + permit portals/open data | Use address/parcel searches when possible. |
| Zoning and future development | City of Miami GIS/Zoning, future land-use layers, Miami-Dade GIS | Web + ArcGIS REST | Helps estimate future tower/view-loss/construction risk around a candidate. |
| Building recertification / milestone inspection | Miami-Dade Recertification Portal, City of Miami recertification pages, Florida Statutes 553.899 | Web + public records request where needed | Critical for older condo buildings and possible repair/amenity disruption risk. |
| Elevator safety | Miami-Dade Office of Elevator Safety, City of Miami elevator certificate pages, Miami Beach Elevator Division, Florida DBPR Bureau of Elevator Safety | Web + public records where needed | Important for high-rise living because elevator issues are a daily-life risk. |
| Property facts | Miami-Dade Property Appraiser | Web search by address/folio | Use for year built, parcel, ownership, and building context. |
| Transit | Miami-Dade Transit / official GTFS feeds if relevant | Web + GTFS data when needed | Evaluate routes, frequency, access, and reliability against the selected profile’s travel modes and anchors. |
| Broadband / mobile connectivity | FCC National Broadband Map | Web + downloadable data | Use for address-level ISP availability and mobile coverage caveats; still verify building wiring and actual resident experience. |
| Demographics | U.S. Census ACS / Census API, Census Reporter as helper | API/web | Use for age, income, renter share, household composition, and single/lifestyle proxies at neighborhood or tract scale. |
| Extreme heat / tree canopy | City of Miami Extreme Heat Plan, Miami-Dade heat vulnerability layers, NWS heat forecasts | Web + GIS where available | Useful for walkability and outdoor comfort, especially car-light lifestyle. |
| Crime and safety | Miami Police crime mapping, Miami Beach crime dashboards, LexisNexis Community Crime Map | Web + downloads where available | Use for relative patterns, not blanket claims that an area is safe or unsafe. |
| Beach water quality | Florida Healthy Beaches Program, Miami Beach water quality pages | Web | Relevant if Miami Beach becomes a weekly lifestyle priority. |

### Tier 2: Market And Listing Sources

Use these for current availability and pricing, but cross-check because listings can be stale, incomplete, promotional, or missing fees.

| Topic | Sources | Query Method | Notes |
|---|---|---|---|
| Active rental listings | Zillow, Apartments.com, Redfin, Realtor.com, RentCafe, property/building websites, broker sites | Web search and page review | No single source is complete. Do not rely on one portal. |
| Building availability | Official building leasing websites and condo listing pages | Web page review | Stronger for managed apartments than scattered condo rentals. |
| Rent comps | Zillow/Redfin market data, listing portals, building pages | Web + downloadable datasets when available | Use as directional, not as final price truth. |
| Fees and move-in cost | Building websites, listing details, leasing offices, condo association notes | Web + user confirmation | Often missing from listing portals; must be confirmed. |

### Tier 3: Qualitative Signal Sources

Use these as signals, not hard facts.

| Topic | Sources | Query Method | Notes |
|---|---|---|---|
| Building reviews | Google Maps, Apartments.com, Yelp, Reddit, BBB | Web review pattern check | Look for repeated patterns: noise, elevators, management, security, flooding, packages. |
| Social/creative scene | Eventbrite, Meetup, Resident Advisor, Dice, venue calendars, Instagram pages, local publications | Web review | Use to compare density and fit, not to make precise claims. |
| Neighborhood trend context | Local news, The Next Miami, Miami Today, development blogs | Web review | Useful for pipeline discovery; verify important claims with official permits/projects when possible. |
| Walkability and amenity access | Walk Score, Google Maps, OpenStreetMap/Overpass, property pages | Web/API where allowed | Walk Score may require licensing/API terms; OSM can support amenity-distance checks. |
| Condo management / financial stress | DBPR condominium complaint resources, condo association docs when obtainable, public records | Web + user-provided docs/public records | Use for older condo rentals where assessments, repairs, reserves, or board dysfunction could disrupt life. |

## Query Strategy

For each serious neighborhood:
1. Pull the current rental range for unit types that match your profile, in budget, from at least two listing sources.
2. Check official flood/storm-surge context.
3. Check sea-level-rise, king-tide, and known street-flooding context when near water or low-lying blocks.
4. Check construction/development pipeline from official permits/projects, zoning/future land-use layers, and local development reporting.
5. Check traffic/commute friction to your anchors using current map/traffic sources and FDOT/FL511 context.
6. Check traffic-volume exposure when air/noise exposure is a major concern.
7. Check public works/311/flooding signals for recurring block-level issues when available.
8. Check broadband/mobile availability if the building becomes a serious work-from-home candidate.
9. Check demographics if social or network fit is being compared across areas.
10. Check crime/safety patterns using official or law-enforcement-fed sources.
11. Check gym access if your profile has a gym anchor or amenity note.
12. Check air quality exposure from traffic corridors, construction, garage/loading areas, and low-floor street context.
13. Score against the weighted rubric and apply construction/flood/air-quality caps.

For each serious building or listing:
1. Capture exact address, floor, view direction, unit type, base rent, fees, parking, and move-in cost.
2. Check active availability on the building site plus at least one independent listing portal.
3. Search building reviews for repeated negative patterns.
4. Check official permits/construction nearby by address and adjacent parcels.
5. Check zoning/future land use around adjacent parcels for future view-loss or tower-construction risk.
6. Check recertification/milestone inspection status for older condo buildings.
7. Check elevator certificates, inspection jurisdiction, and resident reports of elevator reliability for high-rises.
8. Check FEMA/local flood layers, storm surge, sea-level-rise/king-tide context, and garage/parking exposure.
9. Check bedroom/balcony/workspace orientation relative to traffic, construction, garage exits, loading docks, and valet loops.
10. Check broadband/mobile availability and ask about building wiring, outages, and provider options.
11. Estimate all-in monthly cost and annualized spend.
12. Compare directly against your baseline (current home) from the profile.

## Direct API Use

Direct API use is realistic for:
- Socrata/open-data endpoints such as Miami municipal datasets where available
- ArcGIS REST endpoints for GIS layers
- FEMA NFHL map services
- GTFS transit data
- EPA/AirNow/AQS endpoints when no key is needed or the user supplies one
- OpenStreetMap Overpass for amenity checks, subject to rate limits and data-quality caveats
- U.S. Census ACS API for demographics
- FCC National Broadband Map data downloads for broadband/mobile availability
- NOAA sea-level-rise data downloads and NOAA Tides and Currents
- City of Miami ArcGIS REST services for zoning/future land use and other GIS layers
- CDC PLACES/Socrata endpoints for health and heat-related context when relevant

Direct API use is usually not realistic for:
- Zillow rental inventory
- Apartments.com rental inventory
- Realtor.com rental inventory
- Redfin rental inventory
- Google Maps/Places/Routes without an API key
- Walk Score without an API agreement or allowed usage path

## Required Source Logging

For every research-backed recommendation, log:
- date researched
- exact sources used
- source type: official, listing, review, market report, or local news
- key facts pulled
- freshness or update timestamp if available
- confidence level
- unresolved uncertainty
- whether the source supports an API or only web review
