from __future__ import division
import numpy as np
import matplotlib.pyplot as plt
import math
import CoolProp
from scipy.optimize import minimize
import sys
import abc

R_g = 8.314472e-3 # [kJ/mol/K]
k_B = 13.806505 # [kPa * nm**3 / K]

## Simulation constants
#
Nmol = 400 # Number of molecules

## Gromacs uses a different format
#    
N_sites = 1
N_pair = N_sites**2
N_int = 4.

## RDF bins

r_c = 1.4 # [nm]

r = np.linspace(0.002,1.4,num=700) #[nm]

dr = r[1] - r[0]

r = r - dr/2. # I believe the histogram centers are different than the values reported by Gromacs. The deviations are smaller using this approach. But for P it makes the different systems closer together, which is because Pdev gets larger.

class BasePCFR(object):
    __metaclass__ = abc.ABCMeta
    
    def __init__(self, r, RDF, rho, Temp, ref,devU,devP):
        self.r = r
        self.RDF = RDF
        self.rho = rho
        self.Temp = Temp
        self.ref = ref
        if not self.ref:
            self.devU = 0
            self.devP = 0
        else:
            self.devU = devU
            self.devP = devP
#            self.r = r / self.ref.char_length * self.char_length
        
    @abc.abstractmethod
    def get_dUnb(self):
        """ Return the derivative of the nonbonded potential with respect to r """
        return None
        
    @abc.abstractmethod
    def get_Unb(self):
        """ Return the nonbonded potential """
        return None
    
    def get_Unb_ref(self):
        """ Return the nonbonded potential for the reference system """
        if not self.ref:
            #print('This is the reference system')
            self.Unb_ref = self.get_Unb()
        else:
            #print('Different reference system')
            self.Unb_ref = self.ref.get_Unb()
        return self.Unb_ref
    
    def get_deltaUnb(self):
        """ Returns the difference in the nonbonded energy for self and reference."""
        deltaUnb = self.get_Unb() - self.get_Unb_ref()
        deltaUnb[deltaUnb>1e3] = 1e3
        deltaUnb[deltaUnb<-1e3] = -1e3
        return deltaUnb
    
    @abc.abstractmethod
    def get_Ucorr(self):
        """ Return the nonbonded correction to U """
        return None
    
    @abc.abstractmethod
    def get_Pcorr(self):
        """ Return the nonbonded correction to P """
        return None
    
    def get_RDF(self):
        """ Return the radial distribution function """
        return self.RDF
    
    def get_RDF0(self):
        """ Returns the zeroth order RDF """
        self.RDF0 = self.RDF.copy()
        for iTemp, Temp in enumerate(self.Temp):
            self.RDF0[:,iTemp] = np.exp(-self.get_Unb()/Temp)

    def get_rho(self):
        """ Return the vector of density """
        return self.rho
        
    def smooth_RDF(self):
        """ Smooths the RDF, typically has a very small effect on values."""
        RDFs = self.RDF.copy()
        RDFs_smoothed = np.empty(RDFs.shape)
        self.get_RDF0() # Call the function that calculates the zeroth order RDFs
        for i in range(RDFs.shape[1]):
            RDF = RDFs[:,i]
            RDF_non_zero = RDF[RDF>0]
            RDF_zero = self.RDF0[RDF==0,i] #If the value is zero, assign the zeroth order RDF value. This helps for avoid the scaling problem when 0 * rescale = 0.
            if len(RDF_non_zero) > 5:
                # Smooth the first two and last two points differently
                
                RDF_smoothed = RDF_zero
                
                RDF_smoothed = np.append(RDF_smoothed,1./70 * (69.*RDF_non_zero[0] + 4.*RDF_non_zero[1] - 6.*RDF_non_zero[2] + 4.*RDF_non_zero[3] - RDF_non_zero[4]))
                RDF_smoothed = np.append(RDF_smoothed,1./35 * (2.*RDF_non_zero[0] + 27.*RDF_non_zero[1] + 12.*RDF_non_zero[2] - 8.*RDF_non_zero[3] + 2*RDF_non_zero[4]))
                
                for j in range(2,len(RDF_non_zero)-2):
                    RDF_smoothed = np.append(RDF_smoothed,1./35 * (-3.*RDF_non_zero[j-2] + 12.*RDF_non_zero[j-1] + 17.*RDF_non_zero[j] +12.*RDF_non_zero[j+1] - 3*RDF_non_zero[j+2]))
        
                RDF_smoothed = np.append(RDF_smoothed,1./35 * (2.*RDF_non_zero[-1] + 27.*RDF_non_zero[-2] + 12.*RDF_non_zero[-3] - 8.*RDF_non_zero[-4] + 2*RDF_non_zero[-5]))
                RDF_smoothed = np.append(RDF_smoothed,1./70 * (69.*RDF_non_zero[-1] + 4.*RDF_non_zero[-2] - 6.*RDF_non_zero[-3] + 4.*RDF_non_zero[-4] - RDF_non_zero[-5]))    
                            
                RDF_smoothed[RDF_smoothed<0]=0
                RDFs_smoothed[:,i] = RDF_smoothed
            else:
                RDFs_smoothed[:,i] = RDF
        self.RDF = RDFs_smoothed
        return self.RDF
    
    def pred_RDF(self):
        """ Predicts the RDF based on the Unb and Unb_ref"""
        RDF_hat = self.RDF.copy()
        for iTemp, Temp_i in enumerate(self.Temp):
            rescale = np.exp(-self.get_deltaUnb()/Temp_i)
#            plt.plot(self.r,rescale)
#            plt.ylim([0,2])
#            plt.show()
            RDF_hat[:,iTemp] *= rescale
#            plt.plot(self.r,RDF_hat[:,iTemp],label='Predicted')
#            plt.plot(self.r,self.RDF[:,iTemp],label='Reference')
#            plt.ylim([0,2])
#            plt.legend()
#            plt.show()        
        return RDF_hat
       
    def calc_Uint(self,RDF_pair):
        """ The integral portion of the internal energy calculation"""
        Uint = (self.get_Unb()*RDF_pair*self.r**2*dr).sum() # [K*nm^3]
        return Uint
    
    def calc_Utotal(self,RDF_pair):
        """ The total internal energy. Both the integral with the tail corrections. """
        Uint = self.calc_Uint(RDF_pair)
        Ucorr = self.get_Ucorr()
        Utotal = Uint + Ucorr
        Utotal *= 2*math.pi
        return Utotal
    
    def calc_Uhat(self,RDF_state):
        """ Sums up the Utotal contributions for multiple site-site interactions."""
        Uhat = 0
        
        for n in range(0,N_pair):
            
            RDF_pair = RDF_state[:,n]
            
            Uhat += self.calc_Utotal(RDF_pair)
            
        return Uhat
    
    def calc_Ureal(self):
        """ Converts the internal energy value into the appropriate units. Also includes the deviation term from reference."""
        Ureal = []
        RDF_hat = self.pred_RDF() # Predict the RDF before predicting the energy and pressure
        #for rho_state, Temp_state in zip(self.rho, self.Temp):
        for istate, rho_state in enumerate(self.rho):
            RDF_state = RDF_hat[:,istate*N_pair:istate*N_pair+N_pair]
            Ureal.append(R_g * rho_state * self.calc_Uhat(RDF_state) * N_int)
        Ureal = np.array(Ureal) + self.devU
        return Ureal
    
    def calc_Pideal(self):
        """ Returns the ideal gas contribution to pressure."""
        return self.rho * self.Temp * k_B #[kPa]
        
    def calc_Pint(self,RDF_pair):
        """ Returns the integral portion up to the cutoff for pressure."""
        Pint = (self.get_dUnb()*RDF_pair*self.r**3*dr).sum() # [K*nm^3]
        return Pint
        
    def calc_Ptotal(self,RDF_pair):
        """ Combines the integral and tail corrections for pressure."""
        Pint = self.calc_Pint(RDF_pair)
        Pcorr = self.get_Pcorr()
        Ptotal = 0
        Ptotal += Pint
        Ptotal += Pcorr
        Ptotal *= -2./3*math.pi
        return Ptotal
    
    def calc_Phat(self,RDF_state):
        """ Sums up the Ptotal contributions for multiple site-site interactions."""
        Phat = 0
        
        for n in range(0,N_pair):
            
            RDF_pair = RDF_state[:,n]
            
            Phat += self.calc_Ptotal(RDF_pair)
            
        return Phat 
    
    def calc_Preal(self):
        """ Converts the pressure value into the appropriate units. Also includes the deviation term from reference."""
        Preal = []
        RDF_hat = self.pred_RDF() # Predict the RDF before predicting the energy and pressure
        #for rho_state, Temp_state in zip(self.rho, self.Temp):
        for istate, rho_state in enumerate(self.rho):
            RDF_state = RDF_hat[:,istate*N_pair:istate*N_pair+N_pair]
            Preal.append(k_B * rho_state**2 * self.calc_Phat(RDF_state) * N_int)
        Preal += self.calc_Pideal()
        Preal = np.array(Preal) + self.devP
        return Preal
    
    def calc_Z(self):
        """ Returns the compressibility factor. """
        return self.calc_Preal() / self.rho / self.Temp / k_B
            
class Mie(BasePCFR):
    def __init__(self, r, RDF, rho, T, epsilon, sigma, n=12., m =6., ref=None,devU=0,devP=0, **kwargs):
#        if ref: #I should probably make this its own function
#            rmin = (n/m*sigma**(n-m))**(1./(n-m))
#            #r = r / ref.sigma * sigma
#            r = r / ref.calc_rmin() * rmin
#        r_c = np.max(r)
        BasePCFR.__init__(self, r, RDF, rho, T, ref,devU,devP)
        for key in kwargs:
            if key == 'place holder':
                pass                
        self.epsilon = epsilon
        self.sigma = sigma
        self.n = n
        self.m = m
        self.C = (n/(n-m))*(n/m)**(m/(n-m)) # The normalization constant for the Mie potential
        if n == 12.:
            assert (self.C - 4.) < 1e-3, 'The coefficient for LJ 12-6 should be 4'
        self.r_c_plus = r_c / sigma
        
    def calc_rmin(self):
        """ Calculates the rmin for LJ potential """
        sigma,n,m = self.sigma, self.n, self.m
        rmin = (n/m*sigma**(n-m))**(1./(n-m))
        return rmin
        
    def get_Unb(self):
        """
    The Mie potential calculated in [K]. 
    Note that r and sigma must be in the same units. 
    The exponents (n and m) are set to default values of 12 and 6    
    """
        epsilon, sigma, n, m, r, C = self.epsilon, self.sigma, self.n, self.m, self.r, self.C
        U = C*epsilon*((r/sigma)**-n - (r/sigma)**-m)
        U[U>1e8] = np.max(U[U<1e8]) # No need to store extremely large values of U
        return U        
    
    def get_Ucorr(self):
        epsilon, sigma, n, r_c_plus, C = self.epsilon, self.sigma, self.n, self.r_c_plus, self.C
        U = C*epsilon*((1./(n-3.))*r_c_plus**(3.-n) - (1./3.) * r_c_plus **(-3.))*sigma**3. #[K * nm^3]
        return U
    
    def get_dUnb(self):
        """
    The slope of the Mie potential calculated in [K]. 
    Note that r and sigma must be in the same units. 
    The exponents (n and m) are set to default values of 12 and 6    
    """
        epsilon, sigma, n, m, r, C = self.epsilon, self.sigma, self.n, self.m, self.r, self.C
        dU = C*epsilon/sigma*(6.*(r/sigma)**(-m-1.) - n * (r/sigma)**(-n-1.))
        dU[dU>1e8] = np.max(dU[dU<1e8]) # No need to store extremely large values of dU
        return dU

    def get_Pcorr(self): #I need to correct this for m != 6
        epsilon, sigma, n, r_c_plus, C = self.epsilon, self.sigma, self.n, self.r_c_plus, self.C
        P = -C*epsilon* ((n/(n-3.))* r_c_plus ** (3.-n) - 2. * r_c_plus ** (-3.)) * sigma **3 #[K * nm^3]
        return P
        
#class Exp6(BasePCFR):
#    def __init__(self, epsilon, alpha, r_m, RDF, rho, T):
#        BasePCFR.__init__(self, RDF, rho, T)
#        self.epsilon = epsilon
#        self.alpha = alpha
#        self.r_m = r_m
#    
#    def get_ur(self):
#        return [1,2,3]        

 

def main():
    
    model = 'Mie'

    eps_ref = 117.181 #[K]
    sig_ref = 0.380513 #[nm]
    lam_ref = 15.6796
          
    fname = 'H:/PCF-PSO/RDF_Iteration_4_corrected_gromacs_Non_VLE.txt'
    RDFs_highP = np.loadtxt(fname,delimiter='\t')
    RDFs_highP = RDFs_highP[1:,:] 
    
    T_highP = np.array([135, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600])
    T_TraPPE = np.array([135,150,200,300,400,500,600]) #Only performed simulations at a few with TraPPE
    
    U_L_highP_ens = np.array([-15.37811761, -15.17739261, -14.56776761, -13.99989261, -13.46889261, -12.96789261, -12.49721761, -12.03651761, -11.60244261, -11.16036761, -10.74169261])
    Pref_ens = np.array([245.684,583.268,1634.83,2597.47,3474.17,4313.14,5102.52,5876.44,6613.51,7317.49,7996.19])*100. #[kPa] #Simulated results
    UPotoff_ens = np.array([-15.56048586,-15.37048586,-14.78298586,-14.24048586,-13.72048586,-13.24798586,-12.79548586,-12.34548586,-11.91298586,-11.49298586,-11.08548586]) #From Potoff simulation runs
    UTraPPE_ens = np.array([-13.80869237,-13.62916737,-13.06799237,-12.07034237,-11.19046737,-10.37736737,-9.631417372])
                         
    # I think the values for this array are wrong. Should be 12.06117 
    rhoL_highP = np.array([12.06117]*len(T_highP)) #[1/nm**3]
    # Testing out the pressure calculation with a different rho

    
    LJref = Mie(r,RDFs_highP, rhoL_highP, T_highP, eps_ref, sig_ref, lam_ref)
    Udev = U_L_highP_ens - LJref.calc_Ureal()
    Pdev = Pref_ens - LJref.calc_Preal()
    LJref = Mie(r,RDFs_highP, rhoL_highP, T_highP, eps_ref, sig_ref, lam_ref, ref=LJref,devU=Udev,devP=Pdev) #Redefine the reference system
    
    LJhat = lambda eps, sig, lam: Mie(r,RDFs_highP,rhoL_highP, T_highP, eps, sig, lam, ref=LJref,devU=Udev,devP=Pdev)
    Uhat = lambda eps,sig,lam: LJhat(eps,sig,lam).calc_Ureal()
    
    LJTraPPE = Mie(r,RDFs_highP,rhoL_highP,T_highP,98.,0.375,12., ref=LJref,devU=Udev,devP=Pdev)
    UTraPPE = LJTraPPE.calc_Ureal()
    LJPotoff = Mie(r,RDFs_highP, rhoL_highP, T_highP, 121.25, 0.3783, 16., ref=LJref,devU=Udev,devP=Pdev)
    UPotoff = LJPotoff.calc_Ureal()
    
    #This needs to be above the LJref reassignment
    #RDFnosmooth = LJref.get_RDF()
    #Udevnosmooth = Udev.copy()
    #RDFsmooth = LJref.smooth_RDF()
    #Udevsmooth = U_L_highP_ens - LJref.calc_Ureal()
    #plt.plot(r,RDFnosmooth[:,3],label='No smooth')
    #plt.plot(r,RDFsmooth[:,3],label='Smooth')
    #plt.legend()
    #plt.show()
    
    #plt.scatter(T_highP,Udevnosmooth,label='No smooth')
    #plt.scatter(T_highP,Udevsmooth,label='Smooth')
    #plt.legend()
    #plt.show()
    
    RDFref = LJref.pred_RDF()
    RDFPotoff = LJPotoff.pred_RDF()
    RDFTraPPE = LJTraPPE.pred_RDF()
    
    plt.plot(r,RDFref[:,0],'r',label='Ref')
    plt.plot(r,RDFPotoff[:,0],'b',label='Potoff')
    plt.plot(r,RDFTraPPE[:,0],'g',label='TraPPE')
    plt.legend()
    plt.show()
    
    plt.plot(r/LJref.calc_rmin(),RDFref[:,0],'r',label='Ref')
    plt.plot(r/LJPotoff.calc_rmin(),RDFPotoff[:,0],'b',label='Potoff')
    plt.plot(r/LJTraPPE.calc_rmin(),RDFTraPPE[:,0],'g',label='TraPPE')
    plt.legend()
    plt.show()
    
    plt.plot(r/LJref.sigma,RDFref[:,0],'r',label='Ref')
    plt.plot(r/LJPotoff.sigma,RDFPotoff[:,0],'b',label='Potoff')
    plt.plot(r/LJTraPPE.sigma,RDFTraPPE[:,0],'g',label='TraPPE')
    plt.legend()
    plt.show()
    
    plt.plot(r/LJref.calc_rmin(),LJref.get_Unb(),'r',label='Ref')
    plt.plot(r/LJPotoff.calc_rmin(),LJPotoff.get_Unb(),'b',label='Potoff')
    plt.plot(r/LJTraPPE.calc_rmin(),LJTraPPE.get_Unb(),'g',label='TraPPE')
    plt.ylim([-125,125])
    plt.legend()
    plt.show()
    
    plt.plot(r/LJref.sigma,LJref.get_Unb(),'r',label='Ref')
    plt.plot(r/LJPotoff.sigma,LJPotoff.get_Unb(),'b',label='Potoff')
    plt.plot(r/LJTraPPE.sigma,LJTraPPE.get_Unb(),'g',label='TraPPE')
    plt.plot(r/LJhat(100,0.4,12).sigma,LJhat(100,0.4,12).get_Unb(),'k',label='Large')
    plt.ylim([-125,125])
    plt.legend()
    plt.show()
    
    plt.plot(T_highP,U_L_highP_ens,label='Ref')
    plt.plot(T_highP,UPotoff,label='Potoff')
    plt.plot(T_highP,UTraPPE,label='TraPPE')  
    plt.scatter(T_highP,UPotoff_ens,label='Potoff Sim')
    plt.scatter(T_TraPPE,UTraPPE_ens,label='TraPPE Sim')
    plt.legend()
    plt.show()  
    
    Pref = LJref.calc_Preal()
    PTraPPE = LJTraPPE.calc_Preal()
    PPotoff = LJPotoff.calc_Preal()
    
    Zref = LJref.calc_Z()
    ZTraPPE = LJTraPPE.calc_Z()
    ZPotoff = LJPotoff.calc_Z()
    
    Zref_ens = Pref_ens / rhoL_highP / T_highP / k_B
    
    invT_REFPROP = np.array([7.407407407,6.666666667,5,4,3.333333333,2.857142857,2.5,2.222222222,2,1.818181818,1.666666667])
    Z_REFPROP = np.array([0.000100675,1.187527847,3.614046302,4.832284851,5.50639937,5.903366448,6.145583131,6.295335384,6.386973899,6.440663168,6.46883802])
    
    plt.plot(1000./T_highP,Zref,label='Ref')
    plt.plot(1000./T_highP,ZPotoff,label='Potoff')
    plt.plot(1000./T_highP,ZTraPPE,label='TraPPE')
    plt.plot(invT_REFPROP,Z_REFPROP,label='REFPROP')
    plt.scatter(1000./T_highP,Zref_ens,label='Ref Ens')
    plt.legend()
    plt.show()
    
    #class FFComparator(object):
    #    def __init__(self, reference,*args,**kwargs):
    #        self.reference = reference
    #        self.Udev = U_L_highP_ens - self.reference.calc_Ureal()
    #        for key in kwargs:
    #            if key == 'model':
    #                if kwargs[key] == 'LJ':
    #                    self.epsilon = kwargs['epsilon']
    #                    self.sigma = kwargs['sigma']
    #                    self.lam = kwargs['lam']
    #                    perturbed = LennardJones(self.reference.r, self.reference.RDF, self.reference.rho, self.reference.Temp, self.epsilon,self.sigma,self.lam) 
    ##### Maybe I can just modify LennardJones so that if it is a reference it does things slightly differently. Or maybe
    ## they can stay the same since the deltaU would be zero and so the RDF should be constant and Udev would be as well. 
    #### Yeah, I can just have ref as a keyword and if it is a reference then it becomes its' own reference, effectively.        
    #    def process_one(self, epsilon_i_sigma_i):
    #        epsilon_i, sigma_i = epsilon_i_sigma_i
    ##    def process_one(self, parameters):
    ##        self.reference.process(parameters)
    #        U = 30 # From these parameters
    #        P = 300
    #        Uhat = self.reference.calc_Utotal()
    #        return Uhat-U+epsilon_i, P
    #    
    #comp = FFComparator(LJref,epsilon=121.25,sigma=0.3783,lam=16.,model='LJ')
    #import itertools
    #inputs = itertools.product([100,200,300], [0.3,0.4,0.5])
    #
    ##from  multiprocessing import Pool
    ##p = Pool(5)
    #outputs = map(comp.process_one, inputs)
    #print(list(outputs))
    
    #
    #
    #class ref_ff:
    #    def __init__(self, *params):
    #        self.params = None
    ##    def __init__(self, *coeffs):
    ##        self.coeffs = coeffs
    #
    ## The general concept for this code is:
    ## 1) Input:
    ##    a) Reference system
    ##        i) PCF
    ##        ii) Force field parameters
    ##    b) Target system
    ##        i) Force field parameters
    ##    c) State points
    ##        i) Temperature
    ##        ii) Density
    ##    d) Compound details
    ##        i) Molecular weight
    ##       ii) Number/type of interaction sites
    ##    e) Specific type of PCFR
    ## 2) Output:
    ##    a) U
    ##    b) P
    #    

if __name__ == '__main__':
    
    main()