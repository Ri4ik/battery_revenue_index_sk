# Frequency Containment Reserve (FCR) 

Calculating Frequency Containment Reserve (FCR) revenues takes the 6 prices (one for each 4h block) from the results of the daily auction and multiplies them with the marketable power of the battery for this market. More information for this market can be found on the website of the German TSO [TransnetBW](https://www.transnetbw.de/de/strommarkt/systemdienstleistungen/regelreserve){:target="_blank"}.

## Getting the price information
Prices are published daily by [Transmission System Operators](https://www.regelleistung.net/de-de/){:target="_blank"}. The auction works with a pay-as-cleared mechanism and therefore, there exists one price for the whole market area. Each participant offers FCR services in both direct in parallel and therefore only one price for each block exists. We use the column `GERMANY_SETTLEMENTCAPACITY_PRICE_[(EUR/MW)/h]` from the published excel sheet.

## Calculating revenues  
The 6 prices are multiplied with the marketable power for FCR. The marketable power is limited to 80% of the nominal power $P_{bat}$ of the battery system as stated in Formula 3.9 in the prequalification documents chapter(see [PQ-Conditions](http://regelleistung.net/xspproxy/api/StaticFiles/Regelleistung/Infos_f%C3%BCr_Anbieter/Wie_werde_ich_Regelenergieanbieter_Pr%C3%A4qualifikation/Pr%C3%A4qualifikationsbedingungen_FCR_aFRR_mFRR/PQ-Bedingungen-2024_07_05.pdf){:target="_blank"}). 
Thus, the marketable power $P_t$ is calculated as follows:

$$
P_t = \frac{P_{\text{bat}}}{1.25} = 0.8 \cdot P_{\text{bat}} 
$$

Daily revenue is the sum of all 4h blocks in both directions, weighted by the capture rate:  

$$r_{day}^{FCR} = \ cr^{FCR} \cdot \sum_{i = 1}^{6}{p_{i} \cdot P_{t}}$$

Finally, the battery operator has to reserve a quarter hour of the nominal battery power for SOC management. This becomes relevant for the cross-market trading, when we want to use the battery for other revenue streams inparallel. 