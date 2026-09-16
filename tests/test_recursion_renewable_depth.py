import math
import unittest
from decimal import Decimal as D

from scripts.recursion_renewable_depth import decimal_solar, decimal_wind


class RenewableOracleTests(unittest.TestCase):
    def test_solar_degradation_is_lagged_reciprocal_per_half(self):
        values={'K30':1,'K31':100,'K22':1000,'K18':20,'K38':0.5}
        for year in range(1,21):
            values['K'+str(120+year)]=1
            values['K'+str(158+year)]=0 if year<7 else 0.2
            values['K'+str(44+year)]=0
            values['K'+str(82+year)]=1
        get=lambda cell,**kw:D(str(values.get(cell,0)))
        answers,periods=decimal_solar(get)
        self.assertAlmostEqual(float(answers[0]),50/1.1)
        self.assertAlmostEqual(float(answers[1]),50*(1.1**-26+1.1**-27))
        self.assertEqual(answers[3],D(20))
        self.assertEqual(next(x['generation'] for x in periods if x['year']==7 and x['semester']==1),D(50))

    def test_wind_per_turbine_costs_and_scenario_volume_difference(self):
        values={'F38':0.1}
        for col in 'KLMNOPQ':
            for row,value in {33:2,34:3,37:100,41:120,25:100,56:0.6}.items():values[col+str(row)]=value
            for year in range(1,11):
                for row,value in [(183+year,0.5),(84+year,10),(117+year,2),(150+year,1)]:values[col+str(row)]=value
        get=lambda cell,**kw:D(str(values.get(cell,0)))
        answers,_=decimal_wind(get)
        expected=[231,58.66,1589,189,721,10.3*1000/330]
        for got,want in zip(answers,expected):
            self.assertTrue(math.isclose(float(got),want,rel_tol=1e-12,abs_tol=1e-12))


if __name__=='__main__':
    unittest.main()
