"""AR System form definitions: column names, field IDs and enum encodings.

Column names follow the AR System SQL view convention (form fields with spaces
replaced by underscores, e.g. view HPD_Help_Desk). Field IDs marked "documented"
are widely published BMC field IDs; "approximate" ones are best-effort and should
be checked against your own instance (Developer Studio / AR_System_Metadata).
Enum integer values follow the same rule.
"""

# ---------------------------------------------------------------- enums
STATUS = {0: "New", 1: "Assigned", 2: "In Progress", 3: "Pending",
          4: "Resolved", 5: "Closed", 6: "Cancelled"}
IMPACT = {1000: "1-Extensive/Widespread", 2000: "2-Significant/Large",
          3000: "3-Moderate/Limited", 4000: "4-Minor/Localized"}
URGENCY = {1000: "1-Critical", 2000: "2-High", 3000: "3-Medium", 4000: "4-Low"}
PRIORITY = {0: "Critical", 1: "High", 2: "Medium", 3: "Low"}
SERVICE_TYPE = {0: "User Service Restoration", 1: "User Service Request",
                2: "Infrastructure Restoration", 3: "Infrastructure Event",
                4: "Security Incident"}
REPORTED_SOURCE = {1000: "Direct Input", 2000: "Email", 3000: "External Escalation",
                   4000: "Fax", 4200: "Self Service", 5000: "Systems Management",
                   6000: "Phone", 7000: "Voice Mail", 8000: "Walk In", 9000: "Web",
                   10000: "Other"}
STATUS_REASON = {
    1000: "Infrastructure Change", 2000: "Local Site Action Required",
    3000: "Purchase Order Approval", 4000: "Registration Approval",
    5000: "Supplier Delivery", 6000: "Support Contact Hold",
    7000: "Third Party Vendor Action Reqd", 8000: "Client Action Required",
    9000: "Infrastructure Change Request", 11000: "Future Enhancement",
    12000: "Pending Original Incident", 13000: "Client Hold",
    14000: "Monitoring Incident", 15000: "Customer Follow-Up Required",
    16000: "Temporary Corrective Action", 17000: "No Further Action Required",
    18000: "Resolved by Original Incident", 19000: "Automated Resolution Reported",
}
WORK_LOG_TYPE = {
    1000: "Customer Inbound", 2000: "Customer Outbound", 3000: "General Information",
    4000: "Email System", 5000: "Resolution Communications", 6000: "Working Log",
    7000: "Status Update", 8000: "Vendor Communication", 9000: "Chat",
}
VIEW_ACCESS = {0: "Internal", 1: "Public"}
YES_NO = {0: "No", 1: "Yes"}
ASSOC_TYPE = {35000: "Related to", 36000: "Duplicate of", 37000: "Original of",
              38000: "Caused", 39000: "Caused by", 40000: "Resolved by",
              41000: "Impacts", 42000: "Restores"}
PROFILE_STATUS = {0: "Proposed", 1: "Enabled", 2: "Offline", 3: "Obsolete", 4: "Delete"}
CI_STATUS = {0: "Ordered", 1: "Received", 2: "Being Assembled", 3: "Deployed",
             4: "In Repair", 5: "Down", 6: "End of Life", 7: "Transferred",
             8: "Delete", 9: "In Inventory", 10: "On Loan", 11: "Disposed",
             12: "Reserved", 13: "Return to Vendor"}

PRIORITY_MATRIX = {  # (impact, urgency) -> priority
    (1000, 1000): 0, (1000, 2000): 0, (1000, 3000): 1, (1000, 4000): 2,
    (2000, 1000): 0, (2000, 2000): 1, (2000, 3000): 1, (2000, 4000): 2,
    (3000, 1000): 1, (3000, 2000): 1, (3000, 3000): 2, (3000, 4000): 3,
    (4000, 1000): 1, (4000, 2000): 2, (4000, 3000): 3, (4000, 4000): 3,
}

# ---------------------------------------------------------------- tables
# (column, field_id, label, type, enum, confidence)
D, A = "documented", "approximate"

TABLES = {
    "HPD_Help_Desk": ("HPD:Help Desk", [
        ("Entry_ID", 1, "Entry ID", "char", None, D),
        ("Submitter", 2, "Submitter", "char", None, D),
        ("Submit_Date", 3, "Submit Date", "epoch", None, D),
        ("Assignee_Login_ID", 4, "Assignee Login ID", "char", None, D),
        ("Last_Modified_By", 5, "Last Modified By", "char", None, D),
        ("Last_Modified_Date", 6, "Last Modified Date", "epoch", None, D),
        ("Status", 7, "Status", "enum", STATUS, D),
        ("Incident_Number", 1000000161, "Incident Number", "char", None, D),
        ("Description", 1000000000, "Description", "char", None, D),
        ("Detailed_Decription", 1000000151, "Detailed Decription", "char", None, D),
        ("Service_Type", 1000000099, "Service Type", "enum", SERVICE_TYPE, D),
        ("Reported_Source", 1000000215, "Reported Source", "enum", REPORTED_SOURCE, D),
        ("Impact", 1000000163, "Impact", "enum", IMPACT, D),
        ("Urgency", 1000000162, "Urgency", "enum", URGENCY, D),
        ("Priority", 1000000164, "Priority", "enum", PRIORITY, D),
        ("Status_Reason", 1000000881, "Status_Reason", "enum", STATUS_REASON, A),
        ("Person_ID", 1000000080, "Person ID", "char", None, A),
        ("First_Name", 1000000019, "First Name", "char", None, D),
        ("Last_Name", 1000000018, "Last Name", "char", None, D),
        ("Internet_E_mail", 1000000048, "Internet E-mail", "char", None, A),
        ("Phone_Number", 1000000056, "Phone Number", "char", None, A),
        ("VIP", 1000000027, "VIP", "enum", YES_NO, A),
        ("Company", 1000000082, "Company", "char", None, A),
        ("Organization", 1000000010, "Organization", "char", None, A),
        ("Department", 200000006, "Department", "char", None, A),
        ("Site", 260000001, "Site", "char", None, A),
        ("Region", 200000012, "Region", "char", None, A),
        ("Assigned_Support_Company", 1000000251, "Assigned Support Company", "char", None, D),
        ("Assigned_Support_Organization", 1000000014, "Assigned Support Organization", "char", None, D),
        ("Assigned_Group", 1000000217, "Assigned Group", "char", None, D),
        ("Assigned_Group_ID", 1000000079, "Assigned Group ID", "char", None, A),
        ("Assignee", 1000000218, "Assignee", "char", None, D),
        ("Categorization_Tier_1", 1000000063, "Categorization Tier 1", "char", None, D),
        ("Categorization_Tier_2", 1000000064, "Categorization Tier 2", "char", None, D),
        ("Categorization_Tier_3", 1000000065, "Categorization Tier 3", "char", None, D),
        ("Product_Categorization_Tier_1", 200000003, "Product Categorization Tier 1", "char", None, D),
        ("Product_Categorization_Tier_2", 200000004, "Product Categorization Tier 2", "char", None, D),
        ("Product_Categorization_Tier_3", 200000005, "Product Categorization Tier 3", "char", None, D),
        ("Product_Name", 240001002, "Product Name", "char", None, D),
        ("Manufacturer", 240001003, "Manufacturer", "char", None, A),
        ("HPD_CI", 303497300, "HPD_CI", "char", None, A),
        ("HPD_CI_ReconID", 301541700, "HPD_CI_ReconID", "char", None, A),
        ("ServiceCI", 303544300, "ServiceCI", "char", None, A),
        ("ServiceCI_ReconID", 303544200, "ServiceCI_ReconID", "char", None, A),
        ("Reported_Date", 1000000560, "Reported Date", "epoch", None, D),
        ("Responded_Date", 1000005178, "Responded Date", "epoch", None, A),
        ("Last_Resolved_Date", 1000000564, "Last Resolved Date", "epoch", None, D),
        ("Closed_Date", 1000000563, "Closed Date", "epoch", None, A),
        ("Re_Opened_Date", 1000005261, "Re-Opened Date", "epoch", None, A),
        ("Resolution", 1000000156, "Resolution", "char", None, D),
        ("Resolution_Category", 1000002488, "Resolution Category", "char", None, A),
        ("Resolution_Category_Tier_2", 1000003889, "Resolution Category Tier 2", "char", None, A),
        ("Resolution_Category_Tier_3", 1000003890, "Resolution Category Tier 3", "char", None, A),
        ("Group_Transfers", 1000002536, "Group Transfers", "int", None, A),
        ("Total_Transfers", 1000002537, "Total Transfers", "int", None, A),
        ("Major_Incident", 1000003680, "Major Incident", "enum", YES_NO, A),
    ]),
    "HPD_WorkLog": ("HPD:WorkLog", [
        ("Work_Log_ID", 1, "Work Log ID", "char", None, D),
        ("Submitter", 2, "Submitter", "char", None, D),
        ("Submit_Date", 3, "Submit Date", "epoch", None, D),
        ("Incident_Number", 1000000161, "Incident Number", "char", None, D),
        ("Incident_Entry_ID", 1000000182, "Incident Entry ID", "char", None, A),
        ("Work_Log_Type", 1000000170, "Work Log Type", "enum", WORK_LOG_TYPE, A),
        ("Description", 1000000000, "Description", "char", None, D),
        ("Detailed_Description", 1000000151, "Detailed Description", "char", None, A),
        ("Work_Log_Submitter", 1000000159, "Work Log Submitter", "char", None, A),
        ("Work_Log_Submit_Date", 1000000157, "Work Log Submit Date", "epoch", None, A),
        ("View_Access", 1000000761, "View Access", "enum", VIEW_ACCESS, A),
        ("Secure_Work_Log", 1000000762, "Secure Work Log", "enum", YES_NO, A),
        ("Number_of_Attachments", 1000000364, "Number of Attachments", "int", None, A),
        ("z2AF_Work_Log01", 1000000351, "Attachment 1", "char", None, A),
    ]),
    "HPD_HelpDesk_AuditLogSystem": ("HPD:HelpDesk_AuditLogSystem", [
        ("Request_ID", 1, "Request ID", "char", None, D),
        ("Original_Request_ID", 450, "Original Request ID", "char", None, A),
        ("Incident_Number", 1000000161, "Incident Number", "char", None, A),
        ("Audit_Date", 3, "Audit Date", "epoch", None, A),
        ("User", 2, "User", "char", None, A),
        ("Action", 451, "Action", "char", None, A),
        ("Fields_Changed", 452, "Fields Changed", "char", None, A),
        ("Log", 453, "Log", "char", None, A),
    ]),
    "HPD_Associations": ("HPD:Associations", [
        ("Request_ID", 1, "Request ID", "char", None, D),
        ("Submit_Date", 3, "Submit Date", "epoch", None, D),
        ("Request_ID01", 1000000205, "Request ID01", "char", None, A),
        ("Form_Name01", 1000000206, "Form Name01", "char", None, A),
        ("Request_ID02", 1000000210, "Request ID02", "char", None, A),
        ("Form_Name02", 1000000211, "Form Name02", "char", None, A),
        ("Association_Type01", 1000000213, "Association Type01", "enum", ASSOC_TYPE, A),
        ("Request_Description01", 1000000214, "Request Description01", "char", None, A),
    ]),
    "CTM_People": ("CTM:People", [
        ("Person_ID", 1, "Person ID", "char", None, D),
        ("Submit_Date", 3, "Submit Date", "epoch", None, D),
        ("Profile_Status", 7, "Profile Status", "enum", PROFILE_STATUS, D),
        ("First_Name", 1000000019, "First Name", "char", None, D),
        ("Last_Name", 1000000018, "Last Name", "char", None, D),
        ("Full_Name", 1000000017, "Full Name", "char", None, A),
        ("Remedy_Login_ID", 4, "Remedy Login ID", "char", None, A),
        ("Internet_E_mail", 1000000048, "Internet E-mail", "char", None, A),
        ("Phone_Number_Business", 1000000056, "Phone Number Business", "char", None, A),
        ("Company", 1000000001, "Company", "char", None, A),
        ("Organization", 1000000010, "Organization", "char", None, A),
        ("Department", 200000006, "Department", "char", None, A),
        ("Site", 260000001, "Site", "char", None, A),
        ("Job_Title", 1000000044, "Job Title", "char", None, A),
        ("Support_Staff", 1000000025, "Support Staff", "enum", YES_NO, A),
        ("VIP", 1000000027, "VIP", "enum", YES_NO, A),
    ]),
    "CTM_Support_Group": ("CTM:Support Group", [
        ("Support_Group_ID", 1, "Support Group ID", "char", None, D),
        ("Status", 7, "Status", "enum", {0: "Proposed", 1: "Enabled", 2: "Offline", 3: "Obsolete"}, D),
        ("Company", 1000000001, "Company", "char", None, A),
        ("Support_Organization", 1000000014, "Support Organization", "char", None, A),
        ("Support_Group_Name", 1000000015, "Support Group Name", "char", None, A),
        ("Support_Group_Role", 1000000009, "Support Group Role", "char", None, A),
        ("Business_Hours", 1000000400, "Business Hours", "char", None, A),
    ]),
    "CTM_Support_Group_Association": ("CTM:Support Group Association", [
        ("Request_ID", 1, "Request ID", "char", None, D),
        ("Person_ID", 1000000080, "Person ID", "char", None, A),
        ("Login_ID", 4, "Login ID", "char", None, A),
        ("Support_Group_ID", 1000000079, "Support Group ID", "char", None, A),
        ("Support_Group_Name", 1000000015, "Support Group Name", "char", None, A),
        ("Default", 1000000075, "Default", "enum", YES_NO, A),
    ]),
    "BMC_BaseElement": ("BMC.CORE:BMC_BaseElement", [
        ("InstanceId", 179, "InstanceId", "char", None, D),
        ("ReconciliationIdentity", 400129200, "ReconciliationIdentity", "char", None, D),
        ("ClassId", 400079600, "ClassId", "char", None, D),
        ("Name", 400127400, "Name", "char", None, D),
        ("ShortDescription", 400130900, "ShortDescription", "char", None, A),
        ("Category", 200000003, "Category", "char", None, A),
        ("Type", 200000004, "Type", "char", None, A),
        ("Item", 200000005, "Item", "char", None, A),
        ("Model", 240001002, "Model", "char", None, A),
        ("ManufacturerName", 240001003, "ManufacturerName", "char", None, A),
        ("SerialNumber", 200000001, "SerialNumber (Dell service tag)", "char", None, A),
        ("Site", 260000001, "Site", "char", None, A),
        ("AssetLifecycleStatus", 7, "AssetLifecycleStatus", "enum", CI_STATUS, A),
        ("OwnerName", 260140111, "OwnerName", "char", None, A),
        ("Submit_Date", 3, "Submit Date", "epoch", None, D),
    ]),
    "BMC_BaseRelationship": ("BMC.CORE:BMC_BaseRelationship", [
        ("InstanceId", 179, "InstanceId", "char", None, D),
        ("ClassId", 400079600, "ClassId", "char", None, D),
        ("Name", 400127400, "Name", "char", None, D),
        ("Source_ReconciliationIdentity", 400128800, "Source.ReconciliationIdentity", "char", None, A),
        ("Destination_ReconciliationIdentity", 400128900, "Destination.ReconciliationIdentity", "char", None, A),
        ("Source_ClassId", 400128000, "Source.ClassId", "char", None, A),
        ("Destination_ClassId", 400128100, "Destination.ClassId", "char", None, A),
    ]),
}
