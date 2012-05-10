'''
Created on 25 nov. 2009

@author: jean-baptiste.spiese
'''
import os

from octopus.dispatcher import settings


class LicenseManager:
    class License:
        def __init__(self, name, maximum):
            self.name = name
            self.maximum = int(maximum)
            self.used = 0
            self.currentUsingRenderNodes = []

        def __repr__(self):
            return self.name + " : " + str(self.used) + "/" + str(self.maximum) + " on use"

        def reserve(self):
            if self.used < self.maximum:                                   
                self.used += 1
                return True            
            return False   
    
        def release(self):
            self.used -= 1
        
        def setMaxNumber(self,maxNumber):
            self.maximum = maxNumber
    
    
    def __init__(self):
        self.licenses = {}
        self.readLicensesData()
        
        
    def readLicensesData(self):
        if not os.path.exists(settings.FILE_BACKEND_LICENCES_PATH):
            raise Exception("Licenses file missing: %s" % settings.FILE_BACKEND_LICENCES_PATH)
        else:
            fileIn = open(settings.FILE_BACKEND_LICENCES_PATH, "r")
            lines = [line for line in fileIn.readlines() if not line.startswith("#")]
            fileIn.close()
            
            newLicense = None
            for line in lines:
                line = line.strip()
                if line: 
                    newLicense = LicenseManager.License(*line.strip().split(" "))
                    self.licenses[newLicense.name] = newLicense


    def releaseLicenseForRenderNode(self, licenseName, renderNode):        
        try:
            lic = self.licenses[licenseName]
            try:
                rnId = lic.currentUsingRenderNodes.index(renderNode)
                del lic.currentUsingRenderNodes[rnId]
                lic.release()
            except IndexError:
                print "cannot release license %s for renderNode %s" % (licenseName,renderNode)             
        except KeyError:
            print "License %s not found" % licenseName
            return False       
             
             
    def reserveLicenseForRenderNode(self, licenseName,renderNode):      
        try:
            lic = self.licenses[licenseName]
            success = lic.reserve()
            if success:
                lic.currentUsingRenderNodes.append(renderNode)
            return success           
        except KeyError:
            print "License " + licenseName + " not found"
            return False


    def showLicenses(self):
        for lic in self.licenses.values():
            print lic
            
    
    def __repr__(self):
        rep = "{"
        for lic in self.licenses.values():
            rep += repr(lic) + ","
        rep += "}"
        return rep
    

    def setMaxLicensesNumber(self, licenseName, number):
        try:
            lic = self.licenses[licenseName]
            if lic.maximum !=  number:
                lic.setMaxNumber(number)            
        except KeyError:
            print "License %s not found... Creating new entry" % licenseName
            self.licenses[licenseName] = LicenseManager.License(licenseName, number)

