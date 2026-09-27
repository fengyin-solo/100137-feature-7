"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

from app.routers import plank as router_plank
from app.routers import inflow as router_inflow
from app.routers import aeration as router_aeration
from app.routers import chemical as router_chemical
from app.routers import sediment as router_sediment
from app.routers import sludge as router_sludge
from app.routers import effluent as router_effluent
from app.routers import effluent_rules as router_effluent_rules
from app.routers import labtest as router_labtest
from app.routers import reagent as router_reagent
from app.routers import equip as router_equip
from app.routers import pump as router_pump
from app.routers import power as router_power
from app.routers import pipe as router_pipe
from app.routers import lift as router_lift
from app.routers import meter as router_meter
from app.routers import dispatch2 as router_dispatch2
from app.routers import storm as router_storm
from app.routers import pollutant as router_pollutant
from app.routers import material as router_material
from app.routers import license as router_license

ROUTERS = [router_plank, router_inflow, router_aeration, router_chemical, router_sediment, router_sludge, router_effluent, router_effluent_rules, router_labtest, router_reagent, router_equip, router_pump, router_power, router_pipe, router_lift, router_meter, router_dispatch2, router_storm, router_pollutant, router_material, router_license]
