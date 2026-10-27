"""Homepage editorial content (copy carried over from the current site).

Becomes editable in the content admin (A-009, later); until then it lives here.
"""

from __future__ import annotations

from typing import Final

IMG = "img/home/"

HERO_SLIDES: Final = (IMG + "hero-1.webp", IMG + "hero-2.webp", IMG + "hero-3.webp")

# "Explore the atelier" cards (owner, 2026-10-27). A card links to a category or a fixed
# URL; one with neither is shown as "Coming soon". `photos` swaps the fixed image for product
# photos: "newest" (cycles newest to oldest), "priciest" (cycles most expensive first) or
# "random" (one random print per page load). `image` is the fallback when there are none.
OPENING_CARDS: Final = (
    {"title": "Find Fabrics", "status": "Start swiping", "image": IMG + "open-find-fabrics.webp",
     "url": "/find-fabrics/", "photos": "newest"},
    {"title": "Wishlist", "status": "Your saved prints", "image": IMG + "open-wishlist.webp",
     "url": "/wishlist/", "photos": "priciest"},
    {"title": "African Prints", "status": "Shop now", "image": IMG + "open-african-prints.webp",
     "category": "fabrics"},
    {"title": "3D Models", "status": "Coming soon", "image": IMG + "open-3d-models.webp",
     "photos": "random"},
)  # fmt: skip
CARD_CYCLE_LENGTH: Final = 8  # photos per cycling card

STORY_COLLECTIONS: Final = (
    {
        "category": "adire",
        "eyebrow": "Fabric collection",
        "description": "Indigo-rich patterns shaped by the expressive tradition of resist-dyed "
        "cloth.",
    },
    {
        "category": "cushion-covers",
        "eyebrow": "Home décor",
        "description": "Bold African prints designed to bring warmth and rhythm into your living "
        "space.",
    },
)

FEATURED_CATEGORIES: Final = ("sarong-wraps", "adire", "jackets")

# The old "Shop by cut" tab is gone: cut lengths are no longer categories. Fabric is sold
# in fixed bundles at a set price (owner, 2026-10-26).
SHOP_GROUPS: Final = (
    ("print", "Print", "Shop by print", ("ankara", "kente", "satin-chiffon", "ethnic-prints")),
    (
        "category",
        "Category",
        "Shop by category",
        ("fabrics", "clothing", "home-accessories", "adire"),
    ),
)

HOME_CATEGORY_SLUGS: Final = sorted(
    {c["category"] for c in STORY_COLLECTIONS}
    | set(FEATURED_CATEGORIES)
    | {s for *_, slugs in SHOP_GROUPS for s in slugs}
)

# Journal posts (no blog listing page at launch, decision #86).
POSTS: Final = (
    {
        "category": "Promotion/Special Offer",
        "slug": "how-to-make-the-most-of-our-october-sales",
        "title": "How to Make the Most of Our October Sales",
        "date": "2024-10-01",
        "date_label": "October 1, 2024",
        "image": IMG + "post-promo.webp",
        "alt": "Colorful folded African print fabrics",
        "excerpt": "October is here, and so are our amazing sales at GG Afrikan Eleganz! This "
        "month, we're excited to offer exclusive deals on our most-loved prints.",
        "body": (
            ("p", "October is here, and so are our amazing sales at GG Afrikan Eleganz!"),
            (
                "p",
                "This month, we’re excited to offer exclusive discounts on a range of our beautiful African fabrics, clothing, and accessories. Here are some tips on how to make the most of our October sales:",
            ),
            (
                "ul",
                [
                    "Sign Up for Our Newsletter: Be the first to know about our latest promotions and exclusive offers by subscribing to our newsletter. You’ll receive updates directly in your inbox, ensuring you never miss a sale.",
                    "Follow Us on Social Media: Keep an eye on our social media channels for flash sales and special discount codes. We often share exclusive deals with our followers!",
                    "Plan Your Shopping: Browse our collections beforehand to identify the items you want. This will help you make quick decisions during the sale.",
                    "Shop Early: Popular items sell out fast, so make sure to shop early to secure your favorites.",
                    "Combine Orders: If you’re purchasing multiple items, check our shipping policy. You might be eligible for free shipping on orders over $150!",
                ],
            ),
            (
                "p",
                "Don’t miss out on the opportunity to add stunning African-inspired pieces to your wardrobe. Happy shopping!",
            ),
        ),
    },
    {
        "category": "Cultural Insights",
        "slug": "the-cultural-significance-behind-ankara-prints",
        "title": "The Cultural Significance Behind Ankara Prints",
        "date": "2024-10-01",
        "date_label": "Oct 1, 2024",
        "image": IMG + "post-ankara.webp",
        "alt": "Ankara print fabric with a bold geometric pattern",
        "excerpt": "In the vibrant world of African fashion, few fabrics hold as much cultural significance and appeal as Ankara prints. These bold and colorful textiles…",
        "body": (
            (
                "p",
                "In the vibrant world of African fashion, few fabrics hold as much cultural significance and appeal as Ankara prints. These bold and colorful textiles are not only a reflection of African heritage but also a symbol of cultural pride, history, and identity. Over time, Ankara has transcended geographical boundaries, becoming a global fashion statement that’s appreciated by designers and consumers alike. In this article, we’ll delve deep into the cultural significance behind Ankara prints, exploring their origins, their role in African society, and their modern-day impact on the fashion world.",
            ),
            ("h2", "What is Ankara Fabric?"),
            (
                "p",
                "Ankara, also known as “African wax print,” is a cotton fabric with bright, colorful patterns, produced using a wax-resistant dyeing technique. Though commonly associated with West Africa, the fabric’s origin is rooted in the Dutch colonization era when the technique was adapted from Indonesian batik designs.",
            ),
            (
                "p",
                "Historically, African women adopted Ankara prints as a way to express their creativity and cultural identity. Today, the fabric is worn for a variety of occasions, from weddings and ceremonies to everyday wear, making it one of the most versatile fabrics in African fashion.",
            ),
            ("h2", "The History and Evolution of Ankara Prints"),
            (
                "p",
                "Understanding the history of Ankara is essential to appreciate its cultural significance. While its adoption in Africa is relatively recent (dating back to the 19th century), it has become a mainstay in many African countries, including Nigeria, Ghana, and Senegal.",
            ),
            (
                "p",
                "Ankara was originally produced by the Dutch for the Indonesian market. However, when it failed to take off in Indonesia, the Dutch redirected their marketing efforts toward West Africa, where it was embraced wholeheartedly. The fabric’s vibrant colors and unique patterns resonated with African consumers, who began incorporating it into their traditional attire.",
            ),
            (
                "p",
                "Over time, Ankara prints have been used to symbolize various aspects of African culture, including societal status, political beliefs, and even personal emotions. The different designs and patterns often carry deep meanings, making them an important medium of non-verbal communication in many African communities.",
            ),
            ("h2", "The Symbolism of Ankara Prints in African Culture"),
            (
                "p",
                "Ankara is more than just fabric—each design tells a story. The patterns and colors used in Ankara prints often symbolize various elements of African culture, from proverbs and folklore to historical events and societal norms.",
            ),
            (
                "p",
                'For example, some prints are associated with specific ethnic groups, while others represent shared cultural values. One of the most famous patterns is the "Angelina" print, also known as "Dashiki," which is often worn as a symbol of unity and pride in African heritage.',
            ),
            (
                "p",
                "In addition to symbolism, Ankara is also used to mark significant life events. For example, brides often wear specially designed Ankara outfits for their weddings, while families may choose specific patterns for funerals or other ceremonies. These fabrics help reinforce social bonds and cultural identity, providing a visual representation of life’s most important moments.",
            ),
            ("h2", "Ankara in African Celebrations and Ceremonies"),
            (
                "p",
                "Ankara fabrics play a vital role in African celebrations and ceremonies. Weddings, funerals, festivals, and religious events all feature intricate Ankara designs. These fabrics are typically chosen to match the occasion’s theme or mood, and wearing coordinated Ankara outfits often represents solidarity and unity among family members and guests.",
            ),
            (
                "img",
                "img/home/post-ankara-celebration.webp",
                "Wedding guests dressed in coordinated Ankara outfits",
                "Credit: Canva AI Generator",
            ),
            (
                "p",
                "At weddings, brides and their families don colorful Ankara outfits to signify joy and prosperity. Grooms, too, may don Ankara suits or accessories to complement the bride’s attire. This tradition reflects the cultural significance of marriage as a union not only between two individuals but also between two families and communities.",
            ),
            (
                "p",
                "During funerals, specific Ankara prints may be worn to honor the deceased and pay respect to their legacy. Families often choose subdued colors and patterns that reflect the solemnity of the occasion. In some cultures, Ankara prints are also used to communicate social status or the degree of relationship with the deceased.",
            ),
            ("h2", "Ankara Prints and Modern African Fashion"),
            (
                "p",
                "While Ankara prints are deeply rooted in African culture, they have also evolved into a prominent feature of contemporary fashion. Modern designers have embraced Ankara, blending traditional designs with contemporary styles to create garments that appeal to both African and international audiences.",
            ),
            (
                "p",
                "In recent years, Ankara has graced the runways of major fashion shows in cities like Paris, London, and New York. Designers like Stella Jean, Duro Olowu, and Lisa Folawiyo have played key roles in bringing Ankara to the forefront of global fashion. Celebrities like Beyoncé, Rihanna, and Lupita Nyong’o have been spotted wearing Ankara-inspired outfits, further solidifying its status as a global fashion trend.",
            ),
            ("h2", "Ankara and African Identity"),
            (
                "p",
                "One of the most significant aspects of Ankara prints is their role in expressing African identity. For many Africans, wearing Ankara is a way to honor their heritage and proudly display their cultural roots. The fabric serves as a bridge between the past and the present, connecting modern-day fashion with traditional African values.",
            ),
            (
                "p",
                "Moreover, Ankara prints have become a form of resistance against the homogenization of fashion brought about by globalization. By choosing to wear Ankara, Africans and members of the African diaspora can assert their cultural identity and challenge Western beauty standards. This form of self-expression empowers individuals to reclaim their heritage and showcase the richness of African culture on the global stage.",
            ),
            ("h2", "The Role of Ankara in the African Diaspora"),
            (
                "p",
                "The African diaspora has played a crucial role in popularizing Ankara prints outside of Africa. Many Africans living abroad wear Ankara as a way to stay connected to their roots and maintain a sense of cultural pride. In cities like New York, London, and Paris, Ankara fashion shows and events celebrate African heritage, showcasing the beauty and versatility of these fabrics.",
            ),
            (
                "p",
                "In addition, Ankara has become a symbol of Pan-Africanism, uniting people of African descent across the globe. Whether it’s worn at cultural festivals, protests, or everyday life, Ankara represents solidarity and a shared sense of belonging among Africans and the African diaspora.",
            ),
            ("h2", "Ankara Prints and Social Movements"),
            (
                "p",
                "Beyond fashion, Ankara prints have been used as a tool for social and political movements. In many African countries, wearing specific Ankara prints can serve as a form of silent protest or a way to show support for a cause. For example, during elections, political parties often distribute Ankara fabrics emblazoned with their logos and slogans to garner support from the public.",
            ),
            (
                "p",
                "In recent years, Ankara has also been used to promote awareness of social issues such as gender equality, economic empowerment, and environmental sustainability. Designers and activists alike are using Ankara to create clothing lines that raise funds for charitable causes, promote sustainable fashion practices, and challenge societal norms.",
            ),
            ("h2", "The Future of Ankara: Sustainability and Innovation"),
            (
                "p",
                "As the global fashion industry shifts towards sustainability, Ankara prints are poised to play a significant role in the future of eco-friendly fashion. Many African designers are embracing sustainable practices by using locally sourced materials, reducing waste, and employing traditional production methods that have a minimal environmental impact.",
            ),
            (
                "p",
                "In addition, technological advancements are enabling designers to experiment with new ways of creating Ankara prints. Digital printing techniques, for example, allow for more intricate designs and faster production times, while still preserving the authenticity and cultural significance of the fabric.",
            ),
            ("h2", "Conclusion"),
            (
                "p",
                "Ankara prints are more than just beautiful fabrics—they are a testament to Africa’s rich cultural heritage and an enduring symbol of identity, pride, and creativity. From their historical origins to their modern-day prominence, Ankara prints have become a global fashion phenomenon, connecting people across continents and generations. Whether worn for cultural ceremonies or as a bold fashion statement, Ankara remains a powerful representation of African culture and its influence on the world. As the fashion industry continues to evolve, Ankara’s significance will only continue to grow, serving as a reminder of the beauty, resilience, and diversity of African culture.",
            ),
        ),
    },
)
