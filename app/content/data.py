"""Draft page copy. The owner must approve policy and editorial wording before launch."""

from __future__ import annotations

from typing import Final

FAQS: Final = (
    # Questions and answers exactly as displayed on the previous site (template-faq.php).
    (
        "What is GG Afrikan Eleganz?",
        "GG Afrikan Eleganz is a Philadelphia-based online store offering authentic African-inspired fashion, fabrics, and accessories. We celebrate African heritage through contemporary design, bringing you unique pieces that honor tradition while embracing modern style.",
    ),
    (
        "Where do you source your fabrics?",
        "Our fabrics are sourced directly from skilled artisans and reputable suppliers across Africa. We prioritize quality and authenticity, ensuring that each fabric carries the rich cultural significance of its origin.",
    ),
    (
        "Can I order multiple yards of fabric?",
        "Yes! We offer fabrics in various yardage options. Simply select the desired quantity when placing your order. If you need a specific amount, feel free to contact us and we'll do our best to accommodate your request.",
    ),
    (
        "How do I place an order?",
        "Browse our collection, select the items you love, add them to your cart, and proceed to checkout. You'll be guided through a simple and secure checkout process. Once your order is confirmed, you'll receive an email with your order details.",
    ),
    (
        "What payment options do you accept?",
        "We accept all major credit and debit cards, PayPal, and other secure payment methods available at checkout. All transactions are encrypted and processed through secure payment gateways to protect your information.",
    ),
    (
        "What shipping methods are available?",
        "We offer standard and expedited shipping options within the United States. Shipping rates and estimated delivery times are calculated at checkout based on your location and chosen shipping method.",
    ),
    (
        "Do you ship internationally?",
        "Currently, we primarily ship within the United States. However, we are working on expanding our shipping options to serve our international customers. Please contact us for special international shipping requests.",
    ),
    (
        "How can I track my order?",
        "Once your order ships, you'll receive a confirmation email with a tracking number. You can use this number to track your package on the carrier's website, or log into your account on our site to view your order status.",
    ),
    (
        "How long does delivery take?",
        "Standard shipping typically takes 5–7 business days within the United States. Expedited shipping options are available at checkout for faster delivery. Processing time is usually 1–2 business days before shipment.",
    ),
    (
        "What is your return policy?",
        "We want you to be completely satisfied with your purchase. If you're not happy with your order, you may return eligible items within 14 days of delivery. Items must be in their original condition, unworn, and with tags attached. Please visit our Returns page for full details.",
    ),
    (
        "How can I contact customer support?",
        "You can reach our customer support team via email or through the contact form on our website. We strive to respond to all inquiries within 24–48 hours. Visit our Contact Us page for all available options.",
    ),
    (
        "How can I stay informed about new arrivals and promotions?",
        "Subscribe to our newsletter to receive updates on new arrivals, exclusive promotions, and special offers. You can also follow us on social media to stay connected with the latest from GG Afrikan Eleganz.",
    ),
    (
        "Do you offer wholesale or bulk orders?",
        "Yes, we offer wholesale pricing for bulk orders. Whether you're a retailer, event planner, or designer, we'd love to work with you. Please contact us directly to discuss wholesale options and pricing.",
    ),
    (
        "Do you have a physical store location?",
        "We are currently an online-only store based in Philadelphia, PA. This allows us to serve customers across the country with a wide selection of authentic African fashion. Stay tuned for pop-up events and future store locations!",
    ),
)

POLICIES: Final = {
    "shipping": {
        "title": "Shipping Information",
        "lede": "How orders are prepared, dispatched, and delivered.",
        "sections": (
            (
                "Where we ship",
                (
                    "Available destinations are shown at checkout. If your destination is unavailable, contact us before placing an order.",
                ),
            ),
            (
                "Shipping charges",
                (
                    "We offer Standard shipping (USPS Ground Advantage, usually 2–5 business days) and Express shipping (USPS Priority Mail Express, usually 1–2 business days). The price of each is calculated from your address and the weight of your order, and Philadelphia addresses receive a lower local Standard rate.",
                    "The shipping charge is shown before you pay; that displayed amount is the amount charged for shipping.",
                ),
            ),
            (
                "Processing and delivery",
                (
                    "Any processing and delivery estimates shown during checkout are estimates, not guarantees. Weekends, holidays, carrier delays, severe weather, and address problems may affect arrival.",
                    "When tracking is available, it is sent to the email address used for the order.",
                ),
            ),
            (
                "Address changes and delivery problems",
                (
                    "Contact us as soon as possible if an address is incorrect. We cannot promise changes after an order begins processing.",
                    "If tracking reports a delivery problem, contact the carrier and then contact us so we can help review the shipment.",
                ),
            ),
        ),
    },
    "returns": {
        # Previous site's Returns & Exchanges wording (owner, 2026-10-26: no refund rules that
        # were not already in place). Window set to 14 days from delivery by the owner; returns@
        # and support@ addresses removed until confirmed (see PROJECT_STATUS.md).
        "title": "Returns & Exchanges",
        "lede": "Orders are processed Monday through Friday, excluding holidays. Orders placed after 12:00 PM ET will be processed the next business day.",
        "sections": (
            (
                "Return eligibility",
                (
                    "You have 14 days from the date of delivery to return items. To be eligible for a return:",
                    "The item must be unused and in its original condition. It must include all original packaging, tags, and labels. You must provide a receipt or proof of purchase.",
                ),
            ),
            (
                "How to return an item",
                (
                    "Request Return Authorization: Contact us to request a Return Authorization (RA) number.",
                    "Prepare Your Package: Securely package the item, include the RA number and a copy of your receipt.",
                    "Ship Your Return: Send the package to the address provided by our customer service team. Return shipping costs are the customer's responsibility. Shipping fees are non-refundable.",
                ),
            ),
            (
                "Refunds",
                (
                    "After we receive and inspect your return, we will notify you of the approval status of your refund. Approved refunds will be issued to your original payment method within 7-10 business days.",
                    "Shipping fees are non-refundable. Return shipping costs are your responsibility unless the return was due to an error on our part.",
                ),
            ),
            (
                "Exchanges",
                (
                    "We do not offer direct exchanges. To exchange an item, return it for a refund and place a new order for the replacement item.",
                ),
            ),
            (
                "Incorrect or damaged items",
                (
                    "If you receive an incorrect or damaged item, contact us within 7 days of receipt. We will guide you through the process for returning the item and arranging a replacement or refund.",
                ),
            ),
        ),
    },
    "privacy": {
        "title": "Privacy Policy",
        "lede": "How GG Afrikan Eleganz handles information you share with the store.",
        "sections": (
            (
                "Information we collect",
                (
                    "We collect information you provide when you create an account, contact us, join a list, save products, or place an order. This may include contact, delivery, order, preference, and message information.",
                    "We also receive limited technical information needed for security, sessions, fraud prevention, and reliable operation.",
                ),
            ),
            (
                "How we use information",
                (
                    "We use information to provide the store, respond to requests, fulfill transactions, secure accounts, prevent abuse, meet legal obligations, and improve service.",
                ),
            ),
            (
                "Email and text choices",
                (
                    "Marketing email and text messages require separate choices. You may change those choices in your account. Text-message consent is not a condition of purchase; message and data rates may apply.",
                ),
            ),
            (
                "Analytics and cookies",
                (
                    "With your permission, we use Google Analytics to understand how visitors use the store, such as which pages are viewed and which products are added to carts. Google Analytics cookies are only set if you choose Accept, and you can change your choice at any time with the Cookie settings link at the bottom of every page. If your browser sends a Global Privacy Control signal, we treat it as a decline.",
                    "We also use essential cookies that the store needs to work, such as keeping your cart, wishlist, sign-in and security protections. These are not used for advertising.",
                ),
            ),
            (
                "Service providers",
                (
                    "Payments are processed by Stripe and PayPal; we never see or store your full card number. Sales tax is calculated by Stripe Tax using your shipping address and order items. Shipping rates and labels are provided through Shippo and the carrier delivering your order, using your name, shipping address, and parcel details.",
                ),
            ),
            (
                "Sharing and retention",
                (
                    "We share information only with service providers and authorities as needed to operate the store, process transactions, deliver orders, protect rights, or comply with law. We keep it only as long as needed for those purposes and applicable obligations.",
                ),
            ),
            (
                "Reviews, questions, and moderation",
                (
                    "Signed-in customers may post product reviews and questions. We store the post, rating, account, time, and limited technical information used to prevent abuse. A verified-buyer label may show when the account purchased the product.",
                    "Automated checks look for prohibited text and whether a post is relevant to the product. Off-topic posts may be collapsed but remain readable; honest negative reviews are not hidden because of their rating. Staff may review, reply to, hide, restore, or correct an automated decision.",
                    "Text removed by an automated safety check may be kept privately for up to 30 days for appeals and repeat-abuse prevention, then deleted. If the store enables review photos, images will be safely re-encoded and checked for prohibited content; flagged images will not be retained.",
                ),
            ),
            (
                "Your choices",
                (
                    "You may update or delete a new-store account from the account page. Contact us for other privacy requests. Some records may need to be retained for legal, security, tax, fraud-prevention, or transaction purposes.",
                ),
            ),
        ),
    },
    "terms": {
        "title": "Terms & Conditions",
        "lede": "Terms for using this website and purchasing from GG Afrikan Eleganz.",
        "sections": (
            (
                "Store information",
                (
                    "We aim to describe products, availability, colors, and prices accurately. Images and screen settings can affect color appearance, and we may correct errors or update information.",
                ),
            ),
            (
                "Orders",
                (
                    "When you place an order, you are making an offer to purchase our products or services according to these Terms. We retain the right to accept or decline any order at our discretion. If we reject your order, we will inform you and refund any payment made.",
                ),
            ),
            (
                "Acceptable use",
                (
                    "Do not misuse the site, interfere with its operation, attempt unauthorized access, submit unlawful material, or use its content in a way that infringes another person's rights.",
                ),
            ),
            (
                "Intellectual property",
                (
                    "The site's original branding, photographs, writing, and design are owned by GG Afrikan Eleganz or used with permission. Product and cultural references remain subject to their respective rights and traditions.",
                ),
            ),
            (
                "Liability and governing rules",
                (
                    "The site is provided subject to applicable law. Nothing in these terms excludes rights or responsibilities that cannot legally be excluded. Contact us with questions before purchasing.",
                ),
            ),
        ),
    },
}
