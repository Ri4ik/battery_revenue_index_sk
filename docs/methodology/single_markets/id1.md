# Intraday Continuous (ID1) Market
The ID1 index is the weighted average price of all continuous trades executed within the last trading hour of a contract. This index serves to streamline the process of continuous Intraday trading, whereby bids and asks are matched on a second-by-second basis.
## Market Details

* Product Duration: 15 minutes
* Source: EPEX Spot
* Description: 
    * Market area DE-LU
    * Weighted average price of all continuous trades executed within the last trading hour.

## Revenue Calculation
* Determine the best trading profile based on price spread.
* Calculate marketable power: $$P_t = \min\left(\frac{DoD \times E_{\text{nom}}}{t_{\text{market}}}, P_{\text{nom}}\right)$$

* Compute traded energy for buying and selling:
    * Buy: $$E_{\text{buy}} = \frac{P_{(t_{\text{buy}})} \times \Delta t_{\text{market}}}{\eta_{\text{Bat}}}$$
    * Sell: $$E_{\text{sell}} = P_{(t_{\text{sell}})} \times \Delta t_{\text{market}} \times \eta_{\text{Bat}}$$
    
* Update State of Charge (SOC):
    * Buying: $$\Delta SOC_t = \frac{P_{(t_{\text{buy}})} \times \Delta t_{\text{market}}}{E_{\text{bat}}}$$
    * Selling: $$\Delta SOC_t = \frac{-P_{(t_{\text{sell}})} \times \Delta t_{\text{market}}}{E_{\text{bat}}}$$
* Ensure constraints are met:
    * Ageing costs: $$c_{\text{aging}} = \frac{c_{\text{bat}}}{\text{life}_{\text{Bat}} \times \text{cycles}_{\text{day}} \times 365}$$ in €/MWh
    * SOC limits:
        * $$SOC_t = SOC_{(t-1)} + \Delta SOC_t + SOC_t^{\text{fixed}} - SOC_{(t-1)}^{\text{fixed}}$$  
        * $$\min SOC \leq SOC \leq \max SOC$$
    * DoD limits: By calculating the SOC changes between each timestep, we only allow this trade if the maximum DoD is not exceeded.
    * Power constraints: Finally, based on the calculated SOC curve, the SOC changes in the profile are calculated. These are then converted into power values.  
    $$\Delta P = \frac{\text{diff}(SOC_t) \times E_{\text{bat}}}{\Delta t_{\text{market}}}$$
    * We then check that the absolute power usage never exceeds the marketable power
    $$\Delta P \leq P_t$$

## Revenue Formula:
* $$R_{\text{day}} += p_{(t_{\text{sell}})} \times P_{(t_{\text{sell}})} \times \Delta t_{\text{market}} \times \eta_{\text{Bat}} - p_{(t_{\text{buy}})} \times P_{(t_{\text{buy}})} \times \Delta t_{\text{market}} \times \frac{1}{\eta_{\text{Bat}}}$$
* $$R_{day}^{market}=cr^{market}*R_{day}$$
    