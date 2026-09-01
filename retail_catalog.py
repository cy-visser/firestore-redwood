"""
Retail catalog, customer profiles, and synthetic sentiment/feedback pools for Redwood Retail.
"""

# Product Catalog across 5 Retail & Industrial Electronics Categories
CATALOG_ITEMS = [
    # Sensors & Optics
    {"sku": "SKU-OPT-9901", "name": "Industrial Optical Sensor Pro", "category": "Sensors", "unitPrice": 1200.00, "cost": 720.00},
    {"sku": "SKU-OPT-9902", "name": "Precision LiDAR Proximity Scanner", "category": "Sensors", "unitPrice": 2450.00, "cost": 1500.00},
    {"sku": "SKU-OPT-9903", "name": "Infrared Thermal Imaging Module", "category": "Sensors", "unitPrice": 850.00, "cost": 510.00},
    {"sku": "SKU-OPT-9904", "name": "Ultrasonic Flow Meter Transducer", "category": "Sensors", "unitPrice": 620.00, "cost": 380.00},
    {"sku": "SKU-OPT-9905", "name": "Digital Photoelectric Beam Sensor", "category": "Sensors", "unitPrice": 340.00, "cost": 190.00},
    {"sku": "SKU-OPT-9906", "name": "Multi-Axis Gyroscope & Vibration Sensor", "category": "Sensors", "unitPrice": 480.00, "cost": 270.00},

    # Networking & Gateways
    {"sku": "SKU-NET-4420", "name": "Heavy Duty Edge Gateway Router", "category": "Networking", "unitPrice": 4125.00, "cost": 2600.00},
    {"sku": "SKU-NET-4421", "name": "Industrial 10GbE Managed Switch 24-Port", "category": "Networking", "unitPrice": 3200.00, "cost": 2050.00},
    {"sku": "SKU-NET-4422", "name": "Ruggedized 5G Industrial Gateway", "category": "Networking", "unitPrice": 1850.00, "cost": 1150.00},
    {"sku": "SKU-NET-4423", "name": "Industrial PoE+ Injector Hub 8-Port", "category": "Networking", "unitPrice": 750.00, "cost": 420.00},
    {"sku": "SKU-NET-4424", "name": "Secure Fiber Optic Media Converter", "category": "Networking", "unitPrice": 490.00, "cost": 280.00},
    {"sku": "SKU-NET-4425", "name": "Wireless Mesh Bridge Outdoor IP67", "category": "Networking", "unitPrice": 1250.00, "cost": 780.00},

    # Automation & Computing
    {"sku": "SKU-AUT-3101", "name": "Edge AI Inference Accelerator Box", "category": "Edge Computing", "unitPrice": 5600.00, "cost": 3600.00},
    {"sku": "SKU-AUT-3102", "name": "DIN-Rail Embedded Industrial PC", "category": "Edge Computing", "unitPrice": 2200.00, "cost": 1400.00},
    {"sku": "SKU-AUT-3103", "name": "Programmable Logic Controller Master Unit", "category": "Automation", "unitPrice": 1750.00, "cost": 1050.00},
    {"sku": "SKU-AUT-3104", "name": "High-Speed Digital I/O Expansion Module", "category": "Automation", "unitPrice": 420.00, "cost": 240.00},
    {"sku": "SKU-AUT-3105", "name": "Servo Motor Controller 48V High Torque", "category": "Automation", "unitPrice": 980.00, "cost": 590.00},
    {"sku": "SKU-AUT-3106", "name": "HMI Touchscreen Display Panel 12-inch", "category": "Automation", "unitPrice": 1350.00, "cost": 810.00},

    # Power & Environmental
    {"sku": "SKU-PWR-5501", "name": "Uninterruptible Power Supply 3kVA Online", "category": "Power", "unitPrice": 2800.00, "cost": 1750.00},
    {"sku": "SKU-PWR-5502", "name": "Industrial Regulated DC Power Supply 24V", "category": "Power", "unitPrice": 380.00, "cost": 210.00},
    {"sku": "SKU-PWR-5503", "name": "Smart Energy Consumption Monitor 3-Phase", "category": "Power", "unitPrice": 920.00, "cost": 560.00},
    {"sku": "SKU-PWR-5504", "name": "Cabinet Climate Control & Cooling Unit", "category": "Cooling", "unitPrice": 1950.00, "cost": 1200.00},
    {"sku": "SKU-PWR-5505", "name": "Surge Protection Device Class I+II", "category": "Power", "unitPrice": 290.00, "cost": 150.00}
]

# European Cities for Shipping Addresses
SHIPPING_CITIES = [
    {"city": "Amsterdam", "countryCode": "NL", "postalCode": "1016 BS", "province": "North Holland"},
    {"city": "Rotterdam", "countryCode": "NL", "postalCode": "3012 CL", "province": "South Holland"},
    {"city": "Utrecht", "countryCode": "NL", "postalCode": "3511 EV", "province": "Utrecht"},
    {"city": "Eindhoven", "countryCode": "NL", "postalCode": "5611 AZ", "province": "North Brabant"},
    {"city": "Groningen", "countryCode": "NL", "postalCode": "9712 CS", "province": "Groningen"},
    {"city": "Eemshaven", "countryCode": "NL", "postalCode": "9979 XJ", "province": "Groningen"},
    {"city": "Berlin", "countryCode": "DE", "postalCode": "10115", "province": "Berlin"},
    {"city": "Munich", "countryCode": "DE", "postalCode": "80331", "province": "Bavaria"},
    {"city": "Frankfurt", "countryCode": "DE", "postalCode": "60311", "province": "Hesse"},
    {"city": "Hamburg", "countryCode": "DE", "postalCode": "20095", "province": "Hamburg"},
    {"city": "Paris", "countryCode": "FR", "postalCode": "75001", "province": "Ile-de-France"},
    {"city": "Lyon", "countryCode": "FR", "postalCode": "69002", "province": "Auvergne-Rhone-Alpes"},
    {"city": "London", "countryCode": "GB", "postalCode": "EC2M 7PP", "province": "Greater London"},
    {"city": "Brussels", "countryCode": "BE", "postalCode": "1000", "province": "Brussels"}
]

WAREHOUSES = [
    "WH-ROTTERDAM-1", "WH-EEMSHAVEN-2", "WH-EINDHOVEN-3",
    "WH-FRANKFURT-1", "WH-PARIS-2", "WH-LONDON-1"
]

CARRIERS = ["DHL_EXPRESS", "FEDEX_PRIORITY", "UPS_EXPEDITED", "POSTNL_CARGO"]

# Sentiment & Feedback Pools
POSITIVE_FEEDBACK = [
    "Exceptional product quality. The optical sensors calibrated instantly with zero downtime. Fast delivery to our Rotterdam hub!",
    "Outstanding performance on the edge gateways. Hardware exceeded our throughput specs. Fast shipping and smooth procurement.",
    "Very pleased with this purchase. Prompt shipping, seamless invoice settlement, and high build quality.",
    "Everything arrived in pristine condition within 24 hours. Will definitely consolidate our next quarterly hardware orders here.",
    "Flawless integration with our automated PLC systems. Redwood Retail continues to be our most reliable enterprise supplier.",
    "Top-notch industrial gear. Documentation was comprehensive and shipping tracking was spot-on. 5 stars.",
    "Great customer support and high component reliability. Our logistics team is extremely satisfied.",
    "Fast delivery, competitive volume pricing, and excellent packaging. Highly recommended for enterprise procurement."
]

NEUTRAL_FEEDBACK = [
    "Standard delivery time. Hardware works as advertised, though the outer packaging showed minor wear.",
    "Order fulfilled properly. No major issues encountered, standard lead times for industrial optical components.",
    "Products meet basic technical specs. Invoice processing took slightly longer than expected but was resolved.",
    "Average experience. Shipping took 4 business days instead of 2. Components are functioning normally.",
    "Hardware is fine. Website ordering portal was somewhat slow during checkout, but delivery arrived on schedule."
]

NEGATIVE_FEEDBACK = [
    "Extremely disappointed. Order arrived 5 days late, halting our factory line in Munich. Support took 48 hours to reply. Seriously evaluating alternative vendors.",
    "Received 2 damaged sensors due to poor pallet packaging. RMA replacement process is way too slow. High churn risk.",
    "Critical delay on Edge Gateway routers without proactive notice. Account manager was unresponsive. Threatening to cancel our master supplier contract.",
    "Billing dispute: Invoiced price did not reflect our negotiated 20% enterprise discount. Support chatbot could not resolve the ticket.",
    "Third consecutive delayed shipment this quarter. Quality of service has degraded significantly. Looking for other suppliers.",
    "Defective power unit on arrival. Customer support offered no immediate dispatch replacement. Extremely frustrated.",
    "Shipping carrier mishandled the freight. Communication from logistics team was nonexistent. Unsatisfactory experience."
]

COMPLAINT_REASONS = [
    "LATE_DELIVERY", "DEFECTIVE_COMPONENT", "DAMAGED_FREIGHT",
    "BILLING_DISPUTE", "POOR_SUPPORT_RESPONSE", "RMA_DELAY"
]

# Loyalty Program Tiers
LOYALTY_TIERS = [
    {"tier": "NONE", "isMember": 0, "discountRate": 0.0, "weight": 0.35},
    {"tier": "BRONZE", "isMember": 1, "discountRate": 0.05, "weight": 0.25},
    {"tier": "SILVER", "isMember": 1, "discountRate": 0.10, "weight": 0.20},
    {"tier": "GOLD", "isMember": 1, "discountRate": 0.15, "weight": 0.12},
    {"tier": "PLATINUM", "isMember": 1, "discountRate": 0.20, "weight": 0.06},
    {"tier": "ENTERPRISE_VIP", "isMember": 1, "discountRate": 0.25, "weight": 0.02}
]

# Sentiment Score Ranges (-1.0 to +1.0)
FEEDBACK_SENTIMENT_RANGES = {
    "POSITIVE": (0.60, 0.98),
    "NEUTRAL": (-0.15, 0.35),
    "NEGATIVE": (-0.95, -0.40)
}

