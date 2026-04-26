# Automatic Frequency Restoration Reserve Market (aFRR)

In the Automatic Frequency Restoration Reserve (aFRR) market, a battery system can be active in two sub-markets. In the capacity market, the battery offers to reserve power for frequency restoration mechanisms. In the energy market, all participants offer their service to actively use energy to restore the 50 Hz frequency in cases of deviations. More information for this market can be found on the website of the German TSO [TransnetBW](https://www.transnetbw.de/de/strommarkt/systemdienstleistungen/regelreserve){:target="_blank"}.

## aFRR Capacity

Symmetrical bidding is assumed for the positive and negative capacity market. We assume that the marketable power is reserved for 1h delivery. Thus, a 2MWh/1MW system can offer 0.5 MW in each direction, while a 1MWh/1MW system offers 0.25 MW, as we initialize the battery with a SOC of 50%. The market entry barrier of 1 MW is neglected.

### Getting the price information

The prices are published daily by the [Transmission System Operators](https://www.regelleistung.net/de-de/){:target="_blank"}. The auction works with a pay-as-bid mechanism; therefore, multiple prices exist for the market area. We use the column `GERMANY_AVERAGE_CAPACITY_PRICE_[(EUR/MW)/h]` from the published Excel sheet. This column includes the prices for aFRR capacity in both positive and negative directions because, in this market, each participant can choose the direction of bidding (in contrast to FCR). Each price is given per MW and 4h block. The given prices have to be multiplied by 4 to get the prices $p_t$ per MW per hour. We assume parallel bidding in both directions. Therefore, we will use both prices in the following revenue calculation.

$$p_t^{(\pm)} = 4 \cdot p_{4h\ \text{Block}}$$

### Revenue calculation

The 12 prices (6 in each direction) are multiplied by the marketable power for aFRR. To calculate the marketable power for this market, information can be found in chapter 2.7 of the [PQ conditions](https://www.regelleistung.net/xspproxy/api/StaticFiles/Regelleistung/Infos_f%C3%BCr_Anbieter/Wie_werde_ich_Regelenergieanbieter_Pr%C3%A4qualifikation/Pr%C3%A4qualifikationsbedingungen_FCR_aFRR_mFRR/PQ-Bedingungen-2024_07_05.pdf){:target="_blank"}. A battery system operator only has to reserve 1 hour of power delivery if an energy management system exists that can secure 4 hours of aFRR delivery by shifting the announced operating point of the system's grid access point towards the TSO, e.g., through wholesale trades. In that case, costs for these trading activities would occur. Without these SOC management operations, the battery system would have to reserve the full 4 hours of power delivery through capacity reservation. In that case, the system could, in theory, deliver the aFRR service for 4 hours without any wholesale trades. As the final maximum marketable power of the system depends on the specific system under consideration, we consider a 2-hour power reservation as a compromise of both options.

Additionally, to these reservations, we can only offer at most half of the battery’s capacity in each direction, as we want to offer in both directions in parallel. Therefore, the final marketable power $P_{t}^{aFRR}$ can be calculated as follows:

$$P_{t}^{aFRR} = \min\left(\frac{E_{Bat}}{2}*\frac{1}{2h},P_{bat}\right)$$

The daily revenue is calculated as the sum of all 4-hour blocks, including the capture rate $cr^{aFRR}$:

$$ r_{\text{day}}^{\text{aFRR}} = \text{cr}^{\text{aFRR}} \cdot P_t^{\text{aFRR}} \cdot \sum_{i=1}^{6} \left( p_i^+ + p_i^-  \right) $$

## aFRR Energy

For the aFRR energy market, we again neglect entry barriers and assume symmetrical bidding. The marketable power equals the `power_share` assigned to this market multiplied by the nominal battery power.

As aFRR Energy is traded in 15-minute products until 20 minutes before delivery, we apply a simple trading rule:

*   Negative aFRR (Charging): IDA1 price of that quarter-hour minus a 50% margin (using the absolute value of IDA1 prices in case of negative prices).
*   Positive aFRR (Discharging): IDA1 price of that quarter-hour plus a 50% margin (using the absolute value of IDA1 prices in case of negative prices).

This will give us a bidding price for each direction. That is used to determine the threshold power that needs to be requested by the TSO to get us activated. This threshold power is calculated based on the merit order data published by [Regelleistung.net](https://www.regelleistung.net/de-de){:target="_blank"}. We then take the second-by-second activation data published on [Netztransparenz.de](https://www.netztransparenz.de/){:target="_blank"} and calculate the total amount of energy delivered during the quarter-hours.

We stop participating in these auctions when we have reached the allowed cycle limit for this market.

Based on our aFRR Capacity reservation, we have to reserve 1 hour of that power in each direction. If we get activated on the aFRR Energy market and reach the boundaries of these reservations, we will use SOC management to bring our SOC back into normal operation areas. For this, we buy or sell electricity at ID1 prices.

The daily gross revenue is calculated as the sum of all revenues from aFRR Energy activations, minus the costs incurred for SOC management operations. Including the capture rate gives us the net daily revenue.

