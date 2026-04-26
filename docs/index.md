# Welcome to the ISEA Battery Revenue Index!

Together with enspired GmbH, ISEA is developing an index for the German battery market that is intended to transparently map the revenue potential for large-scale storage systems with regard to grid services. The index is currently under development.
In this repository, we provide the code used to calculate the revenues shown on our [website](https://battery-charts.rwth-aachen.de/battery-revenue-index-beta-version-2/) and in the [Grafana dashboards](https://scarica.isea.rwth-aachen.de/battery-revenue-index/d/ddyyugrmnicqof/battery-revenew-index-eng?orgId=1). You can read our decriptive methodology in the subsesction "Methoodology" or find more detailed information about the code base in section "Code". Finally, you can use the code by yourself to recalculate exemplary results that we provide with this repository. If you want to use the code on a larger dataset, you can extend the marketdata with your own data in the same format as the already provided datasets.

## Getting started

* Clone project from GitLab
* Install dependencies from requirements.txt
* Run the calculation_config.py_script
* Find exemplary results in folder "results" in .json format
